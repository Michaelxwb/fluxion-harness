"""物理 Key 检测：Console 侧 HTTP 探针 + 受信容量预占/结算（design §3.4/§3.7；TASK-028）。

- **检测保持 Console 侧 HTTP**：本模块用 httpx 直连成员凭据的模型端点，不引入平台侧 SDK 或
  直连实现；探测原语（`/models`，404/405 回退 `/chat/completions`）只有这一份，既有批量测试
  与本次检测共用，401/超时/传输错误映射不会两处漂移。
- **先占额再探测**：每个目标先经受信容量端口（API-17，TASK-027）预占，成功后才发探测 HTTP；
  预占失败即 `NOT_EXECUTED`（明确未执行：不发请求、不写任何容量行）。结算按证据分档：
  收到远端 HTTP 响应（任意状态码）= 完成证据；连接未建立 = 确定未发送；超时/传输中断 =
  UNKNOWN 保守保留（不假定供方未计费）——与主调用/摘要/提炼同一结算口径。
- **同一物理 Key 合并计量**：探针与主调用/摘要/提炼在 Runtime 侧共享同一 `key_resource_id`
  与额度组（受信端口从 `control.model_*` 解析），本模块只送
  `(model_id, credential_id, attempt_id)`，不自报计量身份。
- **探针输出固定 1**：`max_tokens: 1`（design §3.7），预占按固定载荷取最保守值。
- **不伪称整池健康**：只返回逐项结果，不更新逻辑模型 `last_test_status`、不递增任何 revision、
  不写模型计费审计。
- **续租**：探测的绝对期限就是租约上限（`lease_until = min(now + 租期, deadline_at)`），续租
  结构上不可能延长期限，因此探测路径不调用 renew；端口侧 renew 由 TASK-027 交付。
- **凭据窗口**：凭据快照在预占前读取；期间轮换密钥的竞态与既有批量测试同一窗口（毫秒级、
  同为 ADMIN 操作），预占身份由 Runtime 按当前行解析，不迁就已读快照。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeVar

import httpx
from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_contracts.model_pool import (
    ProbeCallSettlement,
    ProbeCapacityGrant,
    ProbeCapacityRelease,
    ProbeCapacityRequest,
    RemoteCompletionProof,
    SettlementOutcome,
)
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelCredential, ModelDefinition
from ..infrastructure.repositories.model_credential_repository import ModelCredentialRepository
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from ..infrastructure.repositories.model_repository import ModelRepository
from .dto import ModelProbeTarget

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SEC = 5.0
PROBE_CONCURRENCY = 4
PROBE_MAX_OUTPUT_TOKENS = 1
#: 固定探针载荷的最保守预占：协议开销 32 字节 + "ping" 4 字节，× 配置上限倍率 4 + 输出 1
PROBE_RESERVED_TOKENS = 145
MODELS_PATH = "/models"
CHAT_PROBE_PATH = "/chat/completions"
RESERVATIONS_PATH = "/internal/model-capacity/reservations"
CAPACITY_REQUEST_TIMEOUT_SEC = 5.0
TENANT_HEADER = "X-Tenant-Id"
TRACE_HEADER = "X-Trace-Id"

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class ProbeCapacityClient:
    """API-17 受信容量端口的 Console 客户端：预占 + 结算（不做无界重试）。

    - 服务身份、租户与（有则）trace 显式透传；
    - 上游业务码原样上抛；transport/封套/线协议形状异常统一 `COMMON_INTERNAL_ERROR`，
      且不把上游响应正文写入异常或日志（密钥与内部身份都可能出现在正文里）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = CAPACITY_REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {INTERNAL_SERVICE_HEADER: service_token} if service_token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport, headers=headers
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def reserve(
        self, *, tenant_id: str, body: ProbeCapacityRequest, trace_id: str = ""
    ) -> ProbeCapacityGrant:
        data = await self._request(
            "POST",
            RESERVATIONS_PATH,
            tenant_id=tenant_id,
            trace_id=trace_id,
            json=body.model_dump(mode="json"),
        )
        return self._validate(ProbeCapacityGrant, data)

    async def settle(
        self,
        *,
        tenant_id: str,
        reservation_id: uuid.UUID,
        body: ProbeCallSettlement,
        trace_id: str = "",
    ) -> ProbeCapacityRelease:
        data = await self._request(
            "POST",
            f"{RESERVATIONS_PATH}/{reservation_id}/settle",
            tenant_id=tenant_id,
            trace_id=trace_id,
            json=body.model_dump(mode="json"),
        )
        return self._validate(ProbeCapacityRelease, data)

    def _validate(self, model: type[_ResponseT], data: dict[str, Any]) -> _ResponseT:
        try:
            return model.model_validate(data)
        except ValidationError:
            logger.warning("probe_capacity_response_invalid model=%s", model.__name__)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        trace_id: str = "",
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id}
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        try:
            response = await self._client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError:
            # 只记录方法与路径，不带上游正文。
            logger.warning("probe_capacity_request_failed method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        payload = _decode(response)
        if response.status_code >= 400:
            raise AppError(_error_code(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("probe_capacity_envelope_invalid method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return data


@dataclass(frozen=True, slots=True)
class EndpointProbe:
    """一次真实 HTTP 探测的结果：业务状态 + 结算证据（不含容量语义）。"""

    status: Literal["AVAILABLE", "FAILED"]
    error_code: str | None
    outcome: SettlementOutcome
    completion_reference: str | None


def _completion_reference(status_code: int) -> str:
    """远端完成证据的引用（形状证据；权威性由受信端口按 Owner 口径核验）。"""
    return f"probe-http-{status_code}"


async def probe_model_endpoint(
    client: httpx.AsyncClient, *, model_id: str, credential: ModelCredential
) -> EndpointProbe:
    """真实探测：连接与密钥取**成员凭据**（执行入口唯一权威），输出预算固定 1。

    业务映射与既有批量测试同一口径；额外区分结算证据：连接未建立 = 确定未发送（可释放
    占用），其余超时/传输中断 = UNKNOWN（保守保留，不假定供方未计费）。
    """
    headers = {"Authorization": f"Bearer {credential.api_key}"}
    base = credential.base_url.rstrip("/")
    try:
        response = await client.get(f"{base}{MODELS_PATH}", headers=headers)
        if response.status_code in (404, 405):
            response = await client.post(
                f"{base}{CHAT_PROBE_PATH}",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": PROBE_MAX_OUTPUT_TOKENS,
                },
            )
    except httpx.ConnectError:
        return EndpointProbe("FAILED", "MODEL_UNAVAILABLE", "NOT_SENT", None)
    except httpx.TimeoutException:
        return EndpointProbe("FAILED", "MODEL_UNAVAILABLE", "UNKNOWN", None)
    except httpx.TransportError:
        return EndpointProbe("FAILED", "MODEL_UNAVAILABLE", "UNKNOWN", None)

    reference = _completion_reference(response.status_code)
    if 200 <= response.status_code < 300:
        return EndpointProbe("AVAILABLE", None, "SUCCEEDED", reference)
    if response.status_code in (401, 403):
        return EndpointProbe("FAILED", "CREDENTIAL_MISSING", "FAILED", reference)
    if response.status_code >= 500:
        return EndpointProbe("FAILED", "MODEL_UNAVAILABLE", "FAILED", reference)
    return EndpointProbe("FAILED", "COMMON_INTERNAL_ERROR", "FAILED", reference)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """逐项检测结果：只证明被点名的那一名成员（不伪称整池健康）。"""

    model_id: uuid.UUID
    credential_id: uuid.UUID
    status: Literal["AVAILABLE", "FAILED", "NOT_EXECUTED"]
    error_code: str | None
    latency_ms: int
    tested_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "model_id": str(self.model_id),
            "credential_id": str(self.credential_id),
            "status": self.status,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "tested_at": self.tested_at,
        }


@dataclass(frozen=True, slots=True)
class _ReadyTarget:
    """通过本地预检、可以进入容量预占的目标（带成员凭据快照与供方型号）。"""

    model_id: uuid.UUID
    credential_id: uuid.UUID
    provider_model: str
    credential: ModelCredential


@dataclass(frozen=True, slots=True)
class _RefusedTarget:
    """本地预检即失败的目标：没有执行入口，不发 HTTP、不占容量。"""

    model_id: uuid.UUID
    credential_id: uuid.UUID
    error_code: str


class ModelProbeService:
    """物理 Key 检测编排：解析 → 预占 → Console 侧 HTTP 探测 → 按证据结算。"""

    def __init__(self, session: AsyncSession, capacity: ProbeCapacityClient) -> None:
        self._capacity = capacity
        self._models = ModelRepository(session)
        self._credentials = ModelCredentialRepository(session)
        self._deployments = ModelDeploymentRepository(session)

    async def probe(self, tenant_id: str, targets: Sequence[ModelProbeTarget]) -> list[ProbeResult]:
        """逐项返回检测结果（与请求顺序一致）；总并发 ≤4，单次探测预算 5 秒。"""
        plans = await self._resolve(tenant_id, targets)
        probe_id = uuid.uuid4()
        trace_id = current_trace_id()
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SEC) as client:

            async def run(plan: _ReadyTarget | _RefusedTarget) -> ProbeResult:
                async with semaphore:
                    return await self._run_one(tenant_id, probe_id, trace_id, plan, client)

            return list(await asyncio.gather(*(run(plan) for plan in plans)))

    async def _resolve(
        self, tenant_id: str, targets: Sequence[ModelProbeTarget]
    ) -> list[_ReadyTarget | _RefusedTarget]:
        models = {
            model.id: model
            for model in await self._models.list_by_ids(
                tenant_id, [target.model_id for target in targets]
            )
        }
        return [await self._resolve_one(tenant_id, target, models) for target in targets]

    async def _resolve_one(
        self,
        tenant_id: str,
        target: ModelProbeTarget,
        models: dict[uuid.UUID, ModelDefinition],
    ) -> _ReadyTarget | _RefusedTarget:
        """执行入口只有成员凭据（TASK-019 已删逻辑模型旧列）：不满足即没有可探测的成员。"""
        model = models.get(target.model_id)
        if model is None:
            return _RefusedTarget(target.model_id, target.credential_id, "COMMON_NOT_FOUND")
        if not model.enabled:
            return _RefusedTarget(target.model_id, target.credential_id, "MODEL_DISABLED")
        credential = await self._credentials.get(tenant_id, target.credential_id)
        if credential is None:
            return _RefusedTarget(target.model_id, target.credential_id, "COMMON_NOT_FOUND")
        if not credential.enabled or not credential.api_key.strip():
            return _RefusedTarget(target.model_id, target.credential_id, "CREDENTIAL_MISSING")
        member = await self._deployments.find_by_credential(
            tenant_id, target.model_id, target.credential_id
        )
        if member is None or not member.enabled:
            return _RefusedTarget(target.model_id, target.credential_id, "CREDENTIAL_MISSING")
        return _ReadyTarget(target.model_id, target.credential_id, model.model_id, credential)

    async def _run_one(
        self,
        tenant_id: str,
        probe_id: uuid.UUID,
        trace_id: str,
        plan: _ReadyTarget | _RefusedTarget,
        client: httpx.AsyncClient,
    ) -> ProbeResult:
        started = time.monotonic()
        if isinstance(plan, _RefusedTarget):
            return self._result(plan.model_id, plan.credential_id, "FAILED", plan.error_code, started)
        try:
            grant = await self._capacity.reserve(
                tenant_id=tenant_id,
                trace_id=trace_id,
                body=ProbeCapacityRequest(
                    probe_id=probe_id,
                    model_id=plan.model_id,
                    credential_id=plan.credential_id,
                    attempt_id=uuid.uuid4(),
                    deadline_at=datetime.now(UTC) + timedelta(seconds=PROBE_TIMEOUT_SEC),
                    reserved_tokens=PROBE_RESERVED_TOKENS,
                ),
            )
        except AppError as exc:
            # 未能申请容量 = 明确未执行：不发探测 HTTP，也不产生任何容量行
            return self._result(
                plan.model_id, plan.credential_id, "NOT_EXECUTED", str(exc.code), started
            )
        probe = await probe_model_endpoint(
            client, model_id=plan.provider_model, credential=plan.credential
        )
        await self._settle(tenant_id, trace_id, grant, probe)
        return self._result(
            plan.model_id, plan.credential_id, probe.status, probe.error_code, started
        )

    async def _settle(
        self,
        tenant_id: str,
        trace_id: str,
        grant: ProbeCapacityGrant,
        probe: EndpointProbe,
    ) -> None:
        """按证据结算：缺 usage 保守保留预占（探针只证明可用性，不解析供方 usage）。"""
        settlement = ProbeCallSettlement(
            epoch=grant.epoch,
            outcome=probe.outcome,
            usage=None,
            remote_completion=(
                RemoteCompletionProof(
                    kind="PROVIDER_CONFIRMED",
                    reference=probe.completion_reference,
                    completed_at=datetime.now(UTC),
                )
                if probe.completion_reference is not None
                else None
            ),
        )
        try:
            await self._capacity.settle(
                tenant_id=tenant_id,
                reservation_id=grant.reservation_id,
                trace_id=trace_id,
                body=settlement,
            )
        except AppError:
            # 结算失败不改变探测结论；占用保守保留到 Runtime 的有界清扫，且必须可见
            logger.warning(
                "probe_settlement_failed tenant=%s reservation=%s",
                tenant_id,
                grant.reservation_id,
            )

    def _result(
        self,
        model_id: uuid.UUID,
        credential_id: uuid.UUID,
        status: Literal["AVAILABLE", "FAILED", "NOT_EXECUTED"],
        error_code: str | None,
        started: float,
    ) -> ProbeResult:
        return ProbeResult(
            model_id=model_id,
            credential_id=credential_id,
            status=status,
            error_code=error_code,
            latency_ms=int((time.monotonic() - started) * 1000),
            tested_at=datetime.now(UTC),
        )
