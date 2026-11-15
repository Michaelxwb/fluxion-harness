"""模型池 + 容量租约 + 实时凭据：Worker 的 `ModelProvider`（design §3.2.4 / §3.5.6）。

每次模型调用依次做四件事，缺一不可：

1. **按冻结成员解析当前凭据**（Console API-09，`execution_ref.type=TASK`）：密钥只进调用内存，
   快照里没有明文；版本前进或成员-凭据错配是显式结论（`MODEL_CREDENTIAL_CHANGED`）。
2. **以 Task 身份预占 BACKGROUND 容量**（Runtime API-29）：归属是 `task_execution_id`，
   调用方不声明执行方身份。
3. **发请求并保持租约**：续租失约（`MODEL_CALL_LEASE_LOST`）立即取消本地请求，不把已失约的
   结果当作成功。
4. **结算**：有收尾信号才算远端确认结束；缺 usage/失败/无结论一律保守保留预占。

容量不可用（`MODEL_CAPACITY_UNAVAILABLE`）不是终态：转成 `ModelUnavailableError`，交给 Kernel
的既有重试退避。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from muad_agent_core.agent import RunnerCancelled
from muad_agent_core.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
    OpenAICompatibleProvider,
)
from muad_contracts import (
    SnapshotModel,
    TaskCallSettlement,
    TaskCapacityGrant,
    TaskCapacityOwner,
    TaskCapacityReservationRequest,
    TaskLeaseRenewal,
)
from muad_contracts.model_pool import ModelTokenUsage, RemoteCompletionProof
from muad_platform_sdk.types import SecretValue

from ...infrastructure.console_credentials import (
    ConsoleCredentialClient,
    CredentialResolutionError,
    ResolvedCredentials,
)
from ...infrastructure.task_capacity_client import (
    CapacityLeaseLost,
    CapacityUnavailable,
    TaskCapacityClient,
    TaskCapacityError,
)

logger = logging.getLogger("muad_agent_worker.application.agentic.model")

PROVIDER_NAME = "task-model-pool"
#: 预占 token 的保守估算：按请求体字节数折半（中文约 1 token/3 字节、ASCII 约 1/4），
#: 再加上本次允许的最大输出。估少会让并发超发，估多只影响单次让路。
BYTES_PER_TOKEN_FLOOR = 2
MIN_RESERVED_TOKENS = 1
#: 续租失败（无结论）后允许的连续次数：再超就从"没有结论"变成"没有执行权"。
MAX_UNKNOWN_RENEWALS = 3


class ModelPoolError(Exception):
    """模型池无法给出可执行的 provider（确定性配置问题）：错误码直接落终态。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FrozenMember:
    deployment_id: UUID
    credential_id: UUID
    revision: int


def frozen_members(model: SnapshotModel) -> tuple[FrozenMember, ...]:
    """冻结池成员：`deployment_id` 优先，其余按快照顺序（顺序已是不可变的冻结事实）。"""
    members: list[FrozenMember] = []
    for entry in model.pool_members:
        try:
            member = FrozenMember(
                deployment_id=UUID(str(entry["deployment_id"])),
                credential_id=UUID(str(entry["credential_id"])),
                revision=int(entry["revision"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        members.append(member)
    preferred = model.deployment_id
    if preferred is not None:
        members.sort(key=lambda item: item.deployment_id != preferred)
    return tuple(members)


def reserved_tokens(request: ModelRequest) -> int:
    """保守预占：请求体字节数 / 2 + 输出上限；至少 1。"""
    body = json.dumps(
        [
            {
                "role": str(message.role),
                "content": message.content
                if isinstance(message.content, str)
                else json.dumps(message.content, ensure_ascii=False, default=str),
                "tool_calls": [call.name for call in message.tool_calls],
            }
            for message in request.messages
        ],
        ensure_ascii=False,
    )
    tokens = len(body.encode("utf-8")) // BYTES_PER_TOKEN_FLOOR
    return max(MIN_RESERVED_TOKENS, tokens + (request.max_tokens or 0))


@dataclass(frozen=True, slots=True)
class ModelTarget:
    """一个冻结的模型引用（主模型或摘要模型）：非密钥定义 + 冻结成员。"""

    model_id: UUID
    model_name: str
    protocol: str
    params: dict[str, Any]
    members: tuple[FrozenMember, ...]

    @classmethod
    def from_snapshot(cls, model: SnapshotModel) -> ModelTarget:
        definition = model.model_json
        model_name = definition.get("model_id")
        if not isinstance(model_name, str) or not model_name:
            raise ModelPoolError("MODEL_MISSING", "frozen model has no model_id")
        protocol = definition.get("protocol")
        if protocol not in (None, "", "OPENAI"):
            raise ModelPoolError("MODEL_MISSING", f"unsupported model protocol: {protocol!r}")
        members = frozen_members(model)
        if not members:
            raise ModelPoolError("CREDENTIAL_MISSING", "frozen model has no pool member")
        params = definition.get("params")
        return cls(
            model_id=model.id,
            model_name=model_name,
            protocol=str(protocol or "OPENAI"),
            params=dict(params) if isinstance(params, dict) else {},
            members=members,
        )

    @classmethod
    def summary_from_snapshot(cls, model: SnapshotModel) -> ModelTarget | None:
        """摘要模型按冻结引用恢复；缺定义就如实返回 None（该层不跑，绝不退回主模型）。"""
        raw = model.summary_model
        if not isinstance(raw, dict):
            return None
        try:
            summary = SnapshotModel.model_validate(raw)
        except ValueError:
            return None
        try:
            return cls.from_snapshot(summary)
        except ModelPoolError:
            return None


class CapacityModelPool:
    """按 Task 身份调用模型的工厂：每次请求一个容量租约。"""

    def __init__(
        self,
        *,
        capacity: TaskCapacityClient,
        credentials: ConsoleCredentialClient,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        deadline_at: datetime,
        http_client: httpx.AsyncClient,
        renew_interval_sec: float | None = None,
        provider_factory: Callable[..., OpenAICompatibleProvider] | None = None,
    ) -> None:
        self._capacity = capacity
        self._credentials = credentials
        self._tenant_id = tenant_id
        self._actor_user_id = actor_user_id
        self._execution_id = execution_id
        self._deadline_at = deadline_at
        self._http_client = http_client
        self._renew_interval_sec = renew_interval_sec
        # 传输缝：生产用 OpenAI 兼容 provider，契约/单测注入脚本化 provider（两者都是
        # `ModelProvider`，容量/凭据/租约语义不受影响）。
        self._provider_factory = provider_factory or OpenAICompatibleProvider

    async def provider(self, target: ModelTarget) -> CapacityBoundProvider:
        return CapacityBoundProvider(pool=self, target=target)

    async def _resolve(self, target: ModelTarget) -> ResolvedCredentials:
        last: CredentialResolutionError | None = None
        for member in target.members:
            try:
                return await self._credentials.resolve(
                    tenant_id=self._tenant_id,
                    actor_user_id=self._actor_user_id,
                    execution_id=self._execution_id,
                    model_id=target.model_id,
                    deployment_id=member.deployment_id,
                    credential_id=member.credential_id,
                    expected_revision=member.revision,
                )
            except CredentialResolutionError as exc:
                last = exc
                if exc.code in ("MODEL_CREDENTIAL_CHANGED", "TASK_ACCESS_DENIED", "FORBIDDEN"):
                    raise ModelPoolError(exc.code, str(exc)) from exc
        raise ModelPoolError(
            last.code if last is not None else "CREDENTIAL_MISSING",
            str(last) if last is not None else "no frozen member could be resolved",
        )

    async def reserve(self, request: ModelRequest, resolved: ResolvedCredentials) -> TaskCapacityGrant:
        try:
            return await self._capacity.reserve(
                tenant_id=self._tenant_id,
                actor_user_id=self._actor_user_id,
                request=TaskCapacityReservationRequest(
                    call_attempt_id=uuid4(),
                    model_id=resolved.model.model_id,
                    deployment_id=resolved.model.deployment_id,
                    credential_id=resolved.model.credential_id,
                    credential_revision=resolved.model.credential_revision,
                    key_resource_id=resolved.model.key_resource_id,
                    quota_group_id=resolved.model.quota_group_id,
                    owner=TaskCapacityOwner(task_execution_id=self._execution_id),
                    deadline_at=self._deadline_at,
                    reserved_tokens=reserved_tokens(request),
                ),
            )
        except CapacityUnavailable as exc:
            raise ModelUnavailableError("model capacity unavailable") from exc
        except CapacityLeaseLost as exc:
            raise RunnerCancelled("model capacity lease lost before the call") from exc
        except TaskCapacityError as exc:
            raise ModelUnavailableError(f"capacity reservation failed: {exc.code or 'unknown'}") from exc

    async def renew(self, grant: TaskCapacityGrant) -> TaskCapacityGrant:
        return await self._capacity.renew(
            tenant_id=self._tenant_id,
            actor_user_id=self._actor_user_id,
            reservation_id=grant.reservation_id,
            renewal=TaskLeaseRenewal(epoch=grant.epoch, deadline_at=self._deadline_at),
        )

    async def settle(
        self,
        grant: TaskCapacityGrant,
        *,
        outcome: str,
        usage: ModelTokenUsage | None,
        proof: RemoteCompletionProof | None,
    ) -> None:
        """结算失败不得掩盖调用结果，但必须留痕。"""
        try:
            await self._capacity.settle(
                tenant_id=self._tenant_id,
                actor_user_id=self._actor_user_id,
                reservation_id=grant.reservation_id,
                settlement=TaskCallSettlement(
                    epoch=grant.epoch,
                    outcome=outcome,  # type: ignore[arg-type]
                    usage=usage,
                    remote_completion=proof,
                ),
            )
        except TaskCapacityError as exc:
            logger.warning(
                "agentic_model_settle_failed",
                extra={"execution_id": str(self._execution_id), "code": exc.code},
            )

    def provider_for(self, resolved: ResolvedCredentials, target: ModelTarget) -> ModelProvider:
        return self._provider_factory(
            base_url=resolved.model.base_url,
            model=target.model_name,
            api_key=SecretValue(value=resolved.model.api_key, version="db"),
            client=self._http_client,
        )

    @property
    def renew_interval_sec(self) -> float | None:
        return self._renew_interval_sec


class CapacityBoundProvider:
    """一次执行的主模型 provider：每个请求预占容量、保持租约、结算。"""

    def __init__(self, *, pool: CapacityModelPool, target: ModelTarget) -> None:
        self._pool = pool
        self._target = target
        self._resolved: ResolvedCredentials | None = None
        self._inner: ModelProvider | None = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._resolved is None:
            self._resolved = await self._pool._resolve(self._target)  # noqa: SLF001
        if self._inner is None:
            self._inner = self._pool.provider_for(self._resolved, self._target)
        grant = await self._pool.reserve(request, self._resolved)
        inner = self._inner
        call = asyncio.create_task(inner.complete(request))
        try:
            response = await self._await_with_lease(call, grant)
        except RunnerCancelled:
            await self._pool.settle(grant, outcome="UNKNOWN", usage=None, proof=None)
            raise
        except Exception:
            await self._pool.settle(grant, outcome="UNKNOWN", usage=None, proof=None)
            raise
        await self._settle_response(grant, response)
        return response

    async def _await_with_lease(
        self, call: asyncio.Task[ModelResponse], grant: TaskCapacityGrant
    ) -> ModelResponse:
        """等待请求完成；续租失约立即取消本地请求（失约的结果不得当成成功）。"""
        interval = self._pool.renew_interval_sec or _renew_interval(grant)
        unknown_renewals = 0
        while True:
            done, _ = await asyncio.wait({call}, timeout=interval)
            if call in done:
                return await call
            try:
                grant = await self._pool.renew(grant)
                unknown_renewals = 0
            except CapacityLeaseLost as exc:
                await _cancel(call)
                raise RunnerCancelled("model capacity lease lost during the call") from exc
            except CapacityUnavailable as exc:
                # 让路不是失约：有界重试；连续无结论才停手（不把"没有结论"当成可以继续调用）。
                unknown_renewals += 1
                if unknown_renewals >= MAX_UNKNOWN_RENEWALS:
                    await _cancel(call)
                    raise ModelUnavailableError("capacity renewal unavailable") from exc
            except TaskCapacityError as exc:
                unknown_renewals += 1
                if unknown_renewals >= MAX_UNKNOWN_RENEWALS:
                    await _cancel(call)
                    raise ModelUnavailableError(
                        f"capacity renewal failed: {exc.code or 'unknown'}"
                    ) from exc
            interval = _renew_interval(grant)

    async def _settle_response(self, grant: TaskCapacityGrant, response: ModelResponse) -> None:
        if not response.finish_reason:
            await self._pool.settle(grant, outcome="UNKNOWN", usage=None, proof=None)
            return
        usage = (
            ModelTokenUsage(
                input_tokens=response.input_tokens or 0,
                output_tokens=response.output_tokens or 0,
            )
            if response.input_tokens is not None and response.output_tokens is not None
            else None
        )
        await self._pool.settle(
            grant,
            outcome="SUCCEEDED",
            usage=usage,
            proof=RemoteCompletionProof(
                kind="PROVIDER_CONFIRMED",
                reference=_proof_reference(self._target.model_name, response.finish_reason),
                completed_at=datetime.now(UTC),
            ),
        )


def _renew_interval(grant: TaskCapacityGrant) -> float:
    remaining = (grant.lease_until - datetime.now(UTC)).total_seconds()
    return max(1.0, remaining / 2)


def _proof_reference(model_name: str, finish_reason: str) -> str:
    digest = hashlib.sha256(f"{model_name}:{finish_reason}".encode()).hexdigest()[:16]
    return f"{PROVIDER_NAME}:{finish_reason}:{digest}"[:256]


async def _cancel(call: asyncio.Task[Any]) -> None:
    call.cancel()
    try:
        await call
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 —— 取消后的结果不再有意义
        pass


__all__ = [
    "CapacityBoundProvider",
    "CapacityModelPool",
    "FrozenMember",
    "ModelPoolError",
    "ModelTarget",
    "reserved_tokens",
]
