"""B-225 / E-04：真实 `AuditedModelProvider` 调用链接入资源池（真实 PG + 真实 HTTP）。

真实边界（不 mock 存储、租约、密钥解析与模型协议）：

- 真实 Runtime 业务路径：Run 经真实 `RunService.start` 创建（真实快照/事件/终态），执行器由
  真实 `default_executor_factory` 装配；
- 真实 PostgreSQL：`runtime.model_call_lease` / `model_capacity` / `model_dispatch_state` /
  `model_invocation_audit` 与 `control.model_*`（隔离租户，只写/删本租户行）；
- 真实 Console 应用（uvicorn 127.0.0.1 + 真实服务身份门控）提供 API-09 冻结引用解析，轮换
  竞争是真实凭据行 UPDATE 后的真实 409；
- 真实模型 HTTP 探针（uvicorn 127.0.0.1）：记录每次请求的型号/鉴权/正文，并按脚本返回
  429（带 Retry-After）/5xx/断流。

关键断言（Acceptance Contract B-225 / E-04）：

- B-225：预占提交后解析所选 Key（解析时点租约已 ACTIVE）；轮换不一致按"确定未发送"释放并有界
  重选；同会话可换 Key 但型号/上下文一致；429 遵守 Retry-After 与原 deadline；逐 attempt 审计
  可追溯（轮换释放与上游发送各有审计行、各有尝试序号）；
- E-04：429/5xx/断流在 deadline/重试预算内处理；已输出正文不混拼另一轮回复（断流后不重试）；
  不跨型号暗换（所有上游请求同一条冻结模型定义）。
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from muad_agent_core.agent import AgentPolicy, AgentRunner, AgentRunRequest, AgentRunResult
from muad_agent_core.model import (
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelUnavailableError,
)
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.executor import (
    POOL_RESELECTION_LIMIT,
    PoolCallScope,
    PooledModelProvider,
)
from muad_agent_runtime.application.model_allocator import ModelAllocator
from muad_agent_runtime.application.model_capacity_service import ModelCapacityService
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import ResolvedAgent, ResolveDefinitionResponse, ResolvedModel
from muad_contracts.model_pool import derive_model_key_resource_id
from muad_contracts.platform_settings import CapacitySettings, ModelPoolSettings

pytestmark = pytest.mark.integration

MODEL_NAME = "gpt-4o-mini"
PROVIDER_KEY = "openai"
AGENT_KEY = "pool-agent"
def _service_token() -> str:
    """内部服务身份：与本进程 Console/Runtime 同源（`.env`/环境变量），测试不另造一份。"""
    token = SharedSettings().internal_service_token
    assert token, "INTERNAL_SERVICE_TOKEN must be configured for the internal boundary"
    return token

FINAL_TEXT = "pong"
PROMPT_TOKENS = 7
COMPLETION_TOKENS = 5
LEASE_SECONDS = 60
#: 直接驱动 provider 的用例使用短租约，让续租心跳在可观测时间内推进
SHORT_LEASE_SECONDS = 1
HEARTBEAT_SECONDS = 0.05


# --------------------------------------------------------------------------- #
# 真实 HTTP 服务：模型探针 + 真实 Console 应用（API-09）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ProbeCall:
    """探针收到的一次真实模型请求（型号/鉴权/正文/到达时刻）。"""

    model: str
    authorization: str
    messages: list[Any]
    arrived_at: float


class ModelProbe:
    """真实模型 HTTP 探针：逐请求记录，并按脚本决定响应形态。"""

    def __init__(self) -> None:
        self.calls: list[ProbeCall] = []
        self.script: list[str] = []
        self.retry_after_sec: float = 0.4

    def expect(self, *modes: str) -> None:
        self.script.extend(modes)

    def _mode(self) -> str:
        return self.script.pop(0) if self.script else "ok"

    @property
    def model_names(self) -> set[str]:
        return {call.model for call in self.calls}

    @property
    def authorization_keys(self) -> list[str]:
        return [call.authorization.removeprefix("Bearer ") for call in self.calls]


def _probe_app(probe: ModelProbe) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        body = await request.json()
        probe.calls.append(
            ProbeCall(
                model=str(body.get("model")),
                authorization=request.headers.get("authorization", ""),
                messages=list(body.get("messages") or []),
                arrived_at=time.monotonic(),
            )
        )
        mode = probe._mode()
        if mode == "429":
            return JSONResponse(
                {"error": {"message": "rate limited"}},
                status_code=429,
                headers={"Retry-After": str(probe.retry_after_sec)},
            )
        if mode == "500":
            return JSONResponse({"error": {"message": "probe failure"}}, status_code=500)
        if mode.startswith("slow:"):
            await asyncio.sleep(float(mode.split(":", 1)[1]))
            mode = "ok"
        if mode == "break":
            async def chunks() -> AsyncIterator[str]:
                yield 'data: {"choices":[{"delta":{"content":"partial-a"}}]}\n\n'
                yield 'data: {"choices":[{"delta":{"content":"partial-b"}}]}\n\n'

            # 流被切断：既没有 finish_reason 也没有 [DONE]
            return StreamingResponse(chunks(), media_type="text/event-stream")
        return JSONResponse(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": FINAL_TEXT},
                    }
                ],
                "usage": {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS},
            }
        )

    return app


@asynccontextmanager
async def _serve(app: FastAPI) -> AsyncIterator[str]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口（真实 socket，非 ASGI 内存传输）。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


@pytest.fixture
async def model_probe() -> AsyncIterator[tuple[ModelProbe, str]]:
    probe = ModelProbe()
    async with _serve(_probe_app(probe)) as url:
        yield probe, url


@pytest.fixture
async def console_url() -> AsyncIterator[str]:
    """真实 Console 应用（真实 API-09 路由 + 真实服务身份门控 + 真实 PG）。"""
    from muad_console_platform.main import app as console_app

    async with _serve(console_app) as url:
        yield url


# --------------------------------------------------------------------------- #
# 控制面播种：真实模型定义 / 凭据 / 成员
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SeededMember:
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    api_key: str
    digest: str
    revision: int

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        return derive_model_key_resource_id(tenant_id, PROVIDER_KEY, self.digest)


@dataclass(frozen=True, slots=True)
class PoolTenant:
    tenant_id: str
    model_id: uuid.UUID
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    members: tuple[SeededMember, ...]
    base_url: str

    @property
    def first(self) -> SeededMember:
        return self.members[0]


async def _seed_tenant(model_base_url: str, *, member_count: int) -> PoolTenant:
    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    model_id, agent_id, platform_user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    members: list[SeededMember] = []
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, provider_key,"
                " params_json) VALUES (:id, :tenant_id, :key, :name, :provider_model, :provider_key,"
                " CAST(:params AS jsonb))"
            ).bindparams(
                id=model_id,
                tenant_id=tenant_id,
                key=f"model-{uuid.uuid4().hex[:10]}",
                name="pool-model",
                provider_model=MODEL_NAME,
                provider_key=PROVIDER_KEY,
                params='{"temperature": 0.0, "max_tokens": 64}',
            )
        )
        for index in range(member_count):
            credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
            api_key = f"sk-member-{index}-{uuid.uuid4().hex}"
            digest = hashlib.sha256(api_key.encode()).hexdigest()
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
                    " api_key, api_key_digest, max_inflight, enabled, revision)"
                    " VALUES (:id, :tenant_id, :key, :provider_key, :base_url, :api_key, :digest, 5,"
                    " true, 1)"
                ).bindparams(
                    id=credential_id,
                    tenant_id=tenant_id,
                    key=f"key-{index}-{uuid.uuid4().hex[:10]}",
                    provider_key=PROVIDER_KEY,
                    base_url=f"{model_base_url}/v1",
                    api_key=api_key,
                    digest=digest,
                )
            )
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id,"
                    " weight, enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, 1, true)"
                ).bindparams(
                    id=deployment_id,
                    tenant_id=tenant_id,
                    key=f"member-{index}-{uuid.uuid4().hex[:10]}",
                    model_id=model_id,
                    credential_id=credential_id,
                )
            )
            members.append(
                SeededMember(
                    deployment_id=deployment_id,
                    credential_id=credential_id,
                    api_key=api_key,
                    digest=digest,
                    revision=1,
                )
            )
        await session.commit()
    return PoolTenant(
        tenant_id=tenant_id,
        model_id=model_id,
        agent_id=agent_id,
        platform_user_id=platform_user_id,
        members=tuple(members),
        base_url=f"{model_base_url}/v1",
    )


@pytest.fixture
async def pool_tenant(model_probe: tuple[ModelProbe, str]) -> AsyncIterator[PoolTenant]:
    """每用例独立租户 + 两名等权成员；结束只删本租户行，不动共享库其它数据。"""
    _, model_url = model_probe
    tenant = await _seed_tenant(model_url, member_count=2)
    try:
        yield tenant
    finally:
        async with get_session_factory()() as session:
            for statement in (
                "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_dispatch_state WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.memory_consolidation_job WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.memory_command_receipt WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.user_memory_history WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.memory_scope WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.tool_control_outbox WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.tool_result_inbox WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_continuation WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.tool_operation WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_interrupt WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.canonical_event WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_submission WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
                "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


def _member_of(tenant: PoolTenant, credential_id: Any) -> SeededMember:
    """按真实解析载荷里的 credential_id 定位冻结成员（选择结果不预设）。"""
    wanted = uuid.UUID(str(credential_id))
    for member in tenant.members:
        if member.credential_id == wanted:
            return member
    raise AssertionError("resolved credential is not one of the frozen members")


def _member_by_key_identity(tenant: PoolTenant, key_resource_id: Any) -> SeededMember:
    """按租约里的计量身份定位实际被使用的成员（轮换/换 Key 用例的确定性依据）。"""
    wanted = uuid.UUID(str(key_resource_id))
    for member in tenant.members:
        if member.key_resource_id(tenant.tenant_id) == wanted:
            return member
    raise AssertionError("lease key identity is not one of the frozen members")


async def _rotate_credential(tenant: PoolTenant, member: SeededMember) -> SeededMember:
    """真实轮换：同一凭据行换新 Key 值/指纹/版本（旧计量身份不迁移）。"""
    rotated_key = f"{member.api_key}-rotated-{uuid.uuid4().hex[:6]}"
    digest = hashlib.sha256(rotated_key.encode()).hexdigest()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE control.model_credential SET api_key = :api_key, api_key_digest = :digest,"
                " revision = revision + 1, update_time = now() WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(
                api_key=rotated_key,
                digest=digest,
                tenant_id=tenant.tenant_id,
                id=member.credential_id,
            )
        )
        await session.commit()
    return replace(member, api_key=rotated_key, digest=digest, revision=member.revision + 1)


# --------------------------------------------------------------------------- #
# 真实事实读取（证据）
# --------------------------------------------------------------------------- #


async def _lease_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, deployment_id, credential_id, key_resource_id,"
                    " credential_revision, workload, lease_owner, epoch, state, reserved_tokens,"
                    " lease_until, create_time, run_id, quota_group_id"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id AND run_id = :run_id"
                    " ORDER BY create_time, call_attempt_id"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _capacity_row(
    tenant_id: str, resource_type: str, resource_id: uuid.UUID
) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                    " AND resource_id = :resource_id"
                ).bindparams(
                    tenant_id=tenant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
        ).mappings().one_or_none()
    return {} if row is None else dict(row)


async def _capacity_totals(
    tenant_id: str, leases: list[dict[str, Any]]
) -> tuple[int, int]:
    """按租约里出现的**不同 Key 身份**汇总在飞与窗口 token（成员可能被换过）。"""
    inflight = reserved = 0
    for key_resource_id in {uuid.UUID(str(lease["key_resource_id"])) for lease in leases}:
        row = await _capacity_row(tenant_id, "KEY", key_resource_id)
        inflight += int(row.get("inflight", 0))
        reserved += int(row.get("reserved_tokens", 0))
    return inflight, reserved


async def _audit_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, attempt, status, retry_reason, error_code, input_tokens, output_tokens"
                    " FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id AND run_id = :run_id"
                    " ORDER BY attempt, create_time"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _dispatch_rows(tenant_id: str) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT deployment_id, dispatch_count FROM runtime.model_dispatch_state"
                    " WHERE tenant_id = :tenant_id ORDER BY deployment_id"
                ).bindparams(tenant_id=tenant_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _run_row(tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, deadline_at FROM runtime.run_record"
                    " WHERE tenant_id = :tenant_id AND id = :run_id"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().one()
    return dict(row)


async def _await_run_status(
    tenant_id: str, run_id: uuid.UUID, *, expected: set[str], timeout: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        row = await _run_row(tenant_id, run_id)
        if str(row["status"]) in expected:
            return row
        if time.monotonic() >= deadline:
            raise AssertionError(f"run stayed {row['status']!r}; expected {sorted(expected)}")
        await asyncio.sleep(0.05)


async def _credential_row(tenant_id: str, credential_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT api_key, api_key_digest, revision, quota_group_id, base_url"
                    " FROM control.model_credential WHERE tenant_id = :tenant_id AND id = :credential_id"
                ).bindparams(tenant_id=tenant_id, credential_id=credential_id)
            )
        ).mappings().one()
    return dict(row)


# --------------------------------------------------------------------------- #
# API-09 真实客户端的观察层（逐调用直通真实 HTTP，不改解析结果）
# --------------------------------------------------------------------------- #


@dataclass
class ResolveLog:
    payloads: list[dict[str, Any]] = field(default_factory=list)
    leases_at_call: list[list[dict[str, Any]]] = field(default_factory=list)

    def frozen_payloads(self) -> list[dict[str, Any]]:
        return [payload for payload in self.payloads if payload.get("deployment_id")]


class ObservingCredentialsClient:
    """真实 API-09 客户端的观察层：记录载荷与解析时点的真实租约事实。

    `before_resolve` 在真实解析**之前**执行（用于模拟"预占提交后、解析前"的真实轮换）；
    它只做真实 PG 写入，不替换解析结果。
    """

    def __init__(
        self,
        inner: ConsoleCredentialsClient,
        *,
        log: ResolveLog,
        before_resolve: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._inner = inner
        self._log = log
        self._before_resolve = before_resolve

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = ""
    ) -> dict[str, Any]:
        self._log.payloads.append(dict(payload))
        run_id = uuid.UUID(str(payload["execution_ref"]["id"]))
        self._log.leases_at_call.append(await _lease_rows(tenant_id, run_id))
        if self._before_resolve is not None:
            await self._before_resolve(payload)
        return await self._inner.resolve_credentials(
            tenant_id=tenant_id, payload=payload, trace_id=trace_id
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.fixture
async def credentials(console_url: str) -> AsyncIterator[ObservingCredentialsClient]:
    inner = ConsoleCredentialsClient(console_url, service_token=_service_token())
    log = ResolveLog()
    client = ObservingCredentialsClient(inner, log=log)
    try:
        yield client
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# 真实 Run 驱动（真实 RunService + 真实 default_executor_factory）
# --------------------------------------------------------------------------- #


class _PgSettingsClient:
    """Runtime 的 `PlatformSettingsClient` 端口：读真实 PG 的平台设置行。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = ""):
        from dataclasses import asdict

        from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
        from muad_console_platform.application.platform_settings_service import (
            PlatformSettingsService,
        )

        async with get_session_factory()() as session:
            current = await PlatformSettingsService(session).read_current(tenant_id)
            return PlatformSettingsSnapshot(revision=current.revision, settings=asdict(current.settings))


async def _save_settings(
    tenant_id: str, *, model_pool: ModelPoolSettings, capacity: CapacitySettings
) -> None:
    """真实 Console 设置服务写入真实 PG（与 TASK-024 同一口径）。"""
    from muad_console_platform.application.audit_service import AuditActor
    from muad_console_platform.application.platform_settings_service import (
        PlatformSettingsService,
    )

    async with get_session_factory()() as session:
        service = PlatformSettingsService(session)
        current = await service.read_current(tenant_id)
        await service.save(
            tenant_id,
            AuditActor(account_id=uuid.uuid4()),
            current.revision,
            replace(current.settings, model_pool=model_pool, capacity=capacity),
        )
        await session.commit()


def _resolved_definition(tenant: PoolTenant, *, base_url: str | None = None) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key=AGENT_KEY,
            revision=1,
            instructions="You are the pool agent.",
            runtime_config={"temperature": 0.0},
        ),
        model=ResolvedModel(
            id=tenant.model_id,
            revision=1,
            model_id=MODEL_NAME,
            base_url=base_url or tenant.base_url,
            api_key="sk-interim-definition-key",
            params={"temperature": 0.0, "max_tokens": 64},
        ),
    )


async def _start_run(
    tenant: PoolTenant,
    *,
    credentials: ObservingCredentialsClient,
    supervisor: Any,
    conversation_id: uuid.UUID | None = None,
    text: str = "pool chain",
    model_pool: ModelPoolSettings | None = None,
    capacity: CapacitySettings | None = None,
) -> uuid.UUID:
    """真实 RunService.start：真实快照/受理/执行装配（`default_executor_factory`）。"""
    from muad_agent_runtime.application.execution_checkpoint import ExecutionIdentity  # noqa: F401
    from muad_agent_runtime.application.run_service import RunRequest, RunService
    from muad_contracts.runtime import ChannelContext, MessageInput

    from agent_runtime.conftest import FakeResolveClient

    await _save_settings(
        tenant.tenant_id,
        model_pool=model_pool or ModelPoolSettings(input_estimate_multiplier=1.0),
        capacity=capacity or CapacitySettings(model_wait_timeout_sec=1),
    )
    resolve = FakeResolveClient(response=_resolved_definition(tenant))
    request = RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
        conversation_id=conversation_id,
    )
    async with get_session_factory()() as session:
        started = await RunService(
            session,
            resolve,
            "instance-b225",
            _PgSettingsClient(),
            credentials_client=credentials,
            supervisor=supervisor,
        ).start(request, tenant.tenant_id)
        async with asyncio.timeout(30):
            async for _ in started.events:
                pass
        return started.run_id


@pytest.fixture
async def supervisor() -> AsyncIterator[Any]:
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor

    instance = ExecutionSupervisor()
    try:
        yield instance
    finally:
        await instance.close()


# --------------------------------------------------------------------------- #
# 直接驱动池化 provider（轮换/续租/结算分支的聚焦证据）
# --------------------------------------------------------------------------- #


def _policy(*, multiplier: float = 1.0, wait_timeout_sec: int = 1):
    from muad_agent_runtime.application.context_settings import PoolExecutionPolicy

    return PoolExecutionPolicy(
        dispatch_epoch_sec=60,
        input_estimate_multiplier=multiplier,
        interactive_weight=8,
        background_weight=1,
        aging_interval_sec=30,
        model_wait_timeout_sec=wait_timeout_sec,
    )


def _model_request(text: str = "hello pool") -> ModelRequest:
    return ModelRequest(
        model_id=MODEL_NAME,
        messages=(ModelMessage(role=ModelRole.USER, content=text),),
        tools=(),
        temperature=0.0,
        max_tokens=64,
        params={},
    )


@dataclass(slots=True)
class PoolHarness:
    provider: PooledModelProvider
    capacity: ModelCapacityService
    run_id: uuid.UUID
    request: ModelRequest


async def _pool_provider(
    tenant: PoolTenant,
    *,
    credentials: ObservingCredentialsClient,
    run_id: uuid.UUID,
    probe_url: str,
    multiplier: float = 1.0,
    lease_seconds: int = LEASE_SECONDS,
    heartbeat_sec: float = HEARTBEAT_SECONDS,
    reselection_limit: int = POOL_RESELECTION_LIMIT,
) -> PooledModelProvider:
    """按生产装配口径直构池化 provider：真实分配器/容量服务/API-09 客户端/模型 HTTP。"""
    from muad_agent_core.model import OpenAICompatibleProvider

    capacity = ModelCapacityService(get_session_factory, lease_duration_sec=lease_seconds)
    allocator = ModelAllocator(get_session_factory, capacity=capacity)
    scope = PoolCallScope(
        tenant_id=tenant.tenant_id,
        model_id=tenant.model_id,
        run_id=run_id,
        lease_owner="rt-b225",
        policy=_policy(multiplier=multiplier),
        members=tuple(
            _member_ref(member) for member in tenant.members
        ),
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    audit_writer = RuntimeAuditWriter(
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        task_id=None,
        conversation_id=uuid.uuid4(),
        user_id=tenant.platform_user_id,
        session_factory=get_session_factory,
    )
    from muad_agent_runtime.application.model_call_trace import ModelCallTrace

    return PooledModelProvider(
        scope=scope,
        allocator=allocator,
        capacity=capacity,
        credentials=credentials,
        provider_factory=lambda credential: OpenAICompatibleProvider(
            base_url=credential.base_url,
            model=MODEL_NAME,
            api_key=credential.api_key.get_secret_value(),
            timeout_sec=30.0,
        ),
        model=MODEL_NAME,
        audit_writer=audit_writer,
        model_trace=ModelCallTrace(),
        heartbeat_sec=heartbeat_sec,
        reselection_limit=reselection_limit,
    )


def _member_ref(member: SeededMember):
    from muad_agent_runtime.infrastructure.model_dispatch_store import MemberRef

    return MemberRef(
        deployment_id=member.deployment_id, credential_id=member.credential_id, weight=1
    )


async def _seed_run(tenant: PoolTenant) -> uuid.UUID:
    """直构用例需要的真实 Run 行（容量租约的 owner 是真实 FK）。"""
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 0)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id, start_time, deadline_at, execution_epoch)"
                " VALUES (:id, :tenant_id, :conversation_id, :user_id, :agent_id, 'RUNNING', 'pool',"
                " :trace_id, now(), :deadline_at, 1)"
            ).bindparams(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                trace_id=uuid.uuid4().hex,
                deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()
    return run_id


# --------------------------------------------------------------------------- #
# B-225：预占 → 解析所选 Key → 发上游 → 按 usage 结算
# --------------------------------------------------------------------------- #


async def test_b225_reserves_before_key_resolution_then_settles_by_usage(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
    supervisor: Any,
) -> None:
    """预占提交后才解析所选 Key；成功后按**实际 usage** 结算，容量归零。"""
    probe, _ = model_probe
    run_id = await _start_run(pool_tenant, credentials=credentials, supervisor=supervisor)
    row = await _await_run_status(pool_tenant.tenant_id, run_id, expected={"COMPLETED"})
    assert row["status"] == "COMPLETED"

    # 真实上游只收到一次请求：型号/鉴权/正文都来自冻结定义与所选成员的当前 Key
    assert len(probe.calls) == 1
    assert probe.model_names == {MODEL_NAME}
    assert probe.authorization_keys != ["sk-interim-definition-key"]

    # 解析请求携带**冻结三件套**，且解析时点上预占已提交（ACTIVE）并冻结同一 Key 身份
    frozen = credentials_log(credentials).frozen_payloads()
    assert len(frozen) == 1
    member = _member_of(pool_tenant, frozen[0]["credential_id"])
    assert probe.authorization_keys == [member.api_key]
    assert frozen[0]["deployment_id"] == str(member.deployment_id)
    assert frozen[0]["credential_id"] == str(member.credential_id)
    assert frozen[0]["expected_revision"] == member.revision
    at_resolve = credentials_log(credentials).leases_at_call[-1]
    assert [lease["state"] for lease in at_resolve] == ["ACTIVE"]
    assert at_resolve[0]["key_resource_id"] == member.key_resource_id(pool_tenant.tenant_id)
    assert at_resolve[0]["credential_revision"] == member.revision

    # 结算：租约 SETTLED，Key 在飞归零，窗口 token 按实际 usage 替换预占
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert len(leases) == 1
    assert leases[0]["state"] == "SETTLED"
    assert leases[0]["reserved_tokens"] > PROMPT_TOKENS + COMPLETION_TOKENS
    capacity = await _capacity_row(
        pool_tenant.tenant_id, "KEY", member.key_resource_id(pool_tenant.tenant_id)
    )
    assert capacity["inflight"] == 0
    assert capacity["reserved_tokens"] == PROMPT_TOKENS + COMPLETION_TOKENS

    # 逐 attempt 审计：一次尝试一行，实际 usage 落审计
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [(audit["attempt"], audit["status"]) for audit in audits] == [(0, "OK")]
    assert audits[0]["input_tokens"] == PROMPT_TOKENS
    assert audits[0]["output_tokens"] == COMPLETION_TOKENS


def credentials_log(client: ObservingCredentialsClient) -> ResolveLog:
    return client._log  # noqa: SLF001 —— 测试观察层，生产代码不读它


# --------------------------------------------------------------------------- #
# B-225：轮换竞争 → 按"确定未发送"释放 + 有界重选
# --------------------------------------------------------------------------- #


async def test_b225_rotation_race_releases_unsent_and_reselects(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    console_url: str,
    supervisor: Any,
) -> None:
    """预占提交后凭据被轮换：解析返回 409 → 释放该预占（不消费新 Key 的槽位）→ 重选。"""
    probe, _ = model_probe
    rotated = False

    async def rotate_before_first_frozen(payload: dict[str, Any]) -> None:
        nonlocal rotated
        if payload.get("deployment_id") is not None and not rotated:
            rotated = True
            await _rotate_credential(pool_tenant, _member_of(pool_tenant, payload["credential_id"]))

    inner = ConsoleCredentialsClient(console_url, service_token=_service_token())
    credentials = ObservingCredentialsClient(
        inner, log=ResolveLog(), before_resolve=rotate_before_first_frozen
    )
    try:
        run_id = await _start_run(pool_tenant, credentials=credentials, supervisor=supervisor)
        await _await_run_status(pool_tenant.tenant_id, run_id, expected={"COMPLETED"})
    finally:
        await credentials.aclose()

    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert len(leases) == 2, "轮换竞争必须按未发送释放并重新选择"
    first, second = leases
    rotated_member = _member_by_key_identity(pool_tenant, first["key_resource_id"])
    # 第一次尝试：冻结的是**轮换前**的版本，且从未发往上游（释放为"确定未发送"）
    assert first["state"] == "SETTLED"
    assert first["credential_revision"] == 1
    old_identity = rotated_member.key_resource_id(pool_tenant.tenant_id)
    assert first["key_resource_id"] == old_identity
    old_capacity = await _capacity_row(pool_tenant.tenant_id, "KEY", old_identity)
    assert old_capacity["inflight"] == 0 and old_capacity["reserved_tokens"] == 0, (
        "确定未发送必须释放该预占（在飞与窗口计数都归零）"
    )

    # 第二次尝试：预占身份与**实际发送的 Key** 一致（绝不拿新 Key 消费旧 Key 的槽位）
    assert second["state"] == "SETTLED"
    sent_key = probe.authorization_keys[0]
    assert sent_key != rotated_member.api_key, "轮换前的旧 Key 不得再被发送"
    credential = await _credential_row(pool_tenant.tenant_id, second["credential_id"])
    assert hashlib.sha256(sent_key.encode()).hexdigest() == credential["api_key_digest"]
    assert second["credential_revision"] == credential["revision"]
    assert second["key_resource_id"] == derive_model_key_resource_id(
        pool_tenant.tenant_id, PROVIDER_KEY, str(credential["api_key_digest"])
    )
    new_capacity = await _capacity_row(
        pool_tenant.tenant_id, "KEY", uuid.UUID(str(second["key_resource_id"]))
    )
    assert new_capacity["inflight"] == 0

    # 逐 attempt 审计可追溯：轮换释放与上游发送各有审计行、各有尝试序号
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [(audit["status"], audit["retry_reason"]) for audit in audits] == [
        ("RETRY", "CREDENTIAL_CHANGED"),
        ("OK", None),
    ]
    assert [audit["attempt"] for audit in audits] == [0, 1]
    assert len({str(audit["id"]) for audit in audits}) == 2
    # 重试也计一次尝试（ADR-04）：两次尝试都写调度计数
    assert sum(int(row["dispatch_count"]) for row in await _dispatch_rows(pool_tenant.tenant_id)) == 2


async def test_b225_reselection_is_bounded_and_leaks_no_capacity(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    console_url: str,
) -> None:
    """轮换持续发生时有界重选：耗尽上限即显式失败，且每个预占都按未发送释放、无泄漏。"""
    probe, _ = model_probe
    run_id = await _seed_run(pool_tenant)

    async def rotate_before_every_frozen(payload: dict[str, Any]) -> None:
        if payload.get("deployment_id") is not None:
            await _rotate_credential(pool_tenant, _member_of(pool_tenant, payload["credential_id"]))

    inner = ConsoleCredentialsClient(console_url, service_token=_service_token())
    credentials = ObservingCredentialsClient(
        inner, log=ResolveLog(), before_resolve=rotate_before_every_frozen
    )
    try:
        provider = await _pool_provider(
            pool_tenant, credentials=credentials, run_id=run_id, probe_url=pool_tenant.base_url
        )
        with pytest.raises(AppError) as captured:
            await provider.complete(_model_request())
    finally:
        await credentials.aclose()

    assert captured.value.code == str(ErrorCode.MODEL_CREDENTIAL_CHANGED)
    assert probe.calls == [], "有界重选耗尽前不得发出任何上游请求"
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert len(leases) == POOL_RESELECTION_LIMIT + 1
    assert {lease["state"] for lease in leases} == {"SETTLED"}
    for lease in leases:
        capacity = await _capacity_row(
            pool_tenant.tenant_id, "KEY", uuid.UUID(str(lease["key_resource_id"]))
        )
        assert capacity["inflight"] == 0 and capacity["reserved_tokens"] == 0
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [audit["retry_reason"] for audit in audits] == ["CREDENTIAL_CHANGED"] * len(leases)


# --------------------------------------------------------------------------- #
# B-225：同会话可换 Key，但型号与上下文一致
# --------------------------------------------------------------------------- #


async def test_b225_same_conversation_switches_key_but_keeps_model_and_context(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
    supervisor: Any,
) -> None:
    """同一会话两轮：第二轮换到另一名成员（另一把 Key），型号与上下文保持冻结语义。"""
    probe, _ = model_probe
    first_run = await _start_run(pool_tenant, credentials=credentials, supervisor=supervisor)
    first_row = await _await_run_status(pool_tenant.tenant_id, first_run, expected={"COMPLETED"})
    async with get_session_factory()() as session:
        conversation_id = await session.scalar(
            sa.text(
                "SELECT conversation_id FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(tenant_id=pool_tenant.tenant_id, id=first_run)
        )
    assert conversation_id is not None and first_row["status"] == "COMPLETED"
    first_lease = (await _lease_rows(pool_tenant.tenant_id, first_run))[0]
    used = _member_by_key_identity(pool_tenant, first_lease["key_resource_id"])
    assert probe.authorization_keys[0] == used.api_key

    # 第二轮前真实轮换第一轮用的那把 Key：调度计数使第二轮选中另一名成员（同会话、不同 Key）
    await _rotate_credential(pool_tenant, used)
    second_run = await _start_run(
        pool_tenant,
        credentials=credentials,
        supervisor=supervisor,
        conversation_id=uuid.UUID(str(conversation_id)),
        text="pool chain",
    )
    await _await_run_status(pool_tenant.tenant_id, second_run, expected={"COMPLETED"})

    assert len(probe.calls) == 2
    assert probe.model_names == {MODEL_NAME}, "不跨型号暗换"
    assert probe.authorization_keys[0] != probe.authorization_keys[1], "同会话可换 Key"
    other = next(member for member in pool_tenant.members if member != used)
    assert probe.authorization_keys[1] == other.api_key
    # 上下文一致：同一份 SYSTEM 指令与同一轮用户输入（型号/上下文不随 Key 变）
    assert probe.calls[0].messages[0] == probe.calls[1].messages[0]
    assert probe.calls[0].messages[-1] == probe.calls[1].messages[-1]

    second_lease = (await _lease_rows(pool_tenant.tenant_id, second_run))[0]
    assert uuid.UUID(str(first_lease["key_resource_id"])) == used.key_resource_id(
        pool_tenant.tenant_id
    )
    assert uuid.UUID(str(second_lease["key_resource_id"])) == other.key_resource_id(
        pool_tenant.tenant_id
    )


# --------------------------------------------------------------------------- #
# B-225：调用期间续租心跳；失约即停手
# --------------------------------------------------------------------------- #


async def test_b225_lease_heartbeat_renews_during_call(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """调用跨越初始租期时，心跳把租约续到调用结束（租约只前进不倒退）。"""
    probe, _ = model_probe
    probe.expect("slow:1.5")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant,
        credentials=credentials,
        run_id=run_id,
        probe_url=pool_tenant.base_url,
        lease_seconds=SHORT_LEASE_SECONDS,
    )
    started = time.monotonic()
    response = await provider.complete(_model_request())
    elapsed = time.monotonic() - started
    assert response.content == FINAL_TEXT and elapsed >= 1.4

    lease = (await _lease_rows(pool_tenant.tenant_id, run_id))[0]
    assert lease["state"] == "SETTLED"
    extended = lease["lease_until"] - lease["create_time"]
    assert extended > timedelta(seconds=SHORT_LEASE_SECONDS + 0.3), (
        f"心跳必须把租约续到调用结束（实际 {extended}）"
    )


async def test_b225_lease_loss_stops_the_local_call_and_keeps_unknown_occupancy(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """失约（租约被接管/标记 UNKNOWN）立即停手：本地调用取消，占用保留为 UNKNOWN。"""
    probe, _ = model_probe
    probe.expect("slow:3.0")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant,
        credentials=credentials,
        run_id=run_id,
        probe_url=pool_tenant.base_url,
        lease_seconds=SHORT_LEASE_SECONDS,
    )

    async def take_over() -> None:
        """调用在飞时用真实容量端口把租约标记 UNKNOWN（模拟接管/清扫）。"""
        while True:
            leases = await _lease_rows(pool_tenant.tenant_id, run_id)
            if leases and leases[0]["state"] == "ACTIVE":
                await provider._capacity.mark_unknown(  # noqa: SLF001 —— 测试注入真实接管事实
                    tenant_id=pool_tenant.tenant_id,
                    call_attempt_id=uuid.UUID(str(leases[0]["call_attempt_id"])),
                    epoch=int(leases[0]["epoch"]),
                    lease_owner=str(leases[0]["lease_owner"]),
                )
                return
            await asyncio.sleep(0.02)

    takeover = asyncio.create_task(take_over())
    started = time.monotonic()
    with pytest.raises(ModelUnavailableError):
        await provider.complete(_model_request())
    elapsed = time.monotonic() - started
    await takeover

    assert elapsed < 2.5, f"失约必须立即停手（实际 {elapsed:.2f}s，探针延迟 3.0s）"
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["UNKNOWN"]
    inflight, reserved = await _capacity_totals(pool_tenant.tenant_id, leases)
    assert inflight == 1 and reserved > 0, "失约不等于远端已停：UNKNOWN 保留占用"
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [audit["status"] for audit in audits] == ["RETRY"], "停手的尝试不得落成 OK"


# --------------------------------------------------------------------------- #
# E-04：429 遵守 Retry-After 与原 deadline
# --------------------------------------------------------------------------- #


async def _runner(provider: PooledModelProvider, *, deadline_ms: int, retries: int) -> AgentRunner:
    """真实 AgentRunner：429/5xx/断流的处理都在它的重试预算与 deadline 之内。"""
    return AgentRunner(provider=provider, registry=ToolRegistry())


async def _drive_runner(
    provider: PooledModelProvider,
    *,
    deadline_ms: int,
    retries: int,
    text: str = "pool chain",
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> AgentRunResult:
    runner = await _runner(provider, deadline_ms=deadline_ms, retries=retries)
    return await runner.run(
        AgentRunRequest(
            model_id=MODEL_NAME,
            instructions="You are the pool agent.",
            messages=(ModelMessage(role=ModelRole.USER, content=text),),
            policy=AgentPolicy(deadline_ms=deadline_ms, max_model_retries=retries),
            temperature=0.0,
            max_tokens=64,
            params={},
        ),
        on_delta=on_delta,
    )


async def test_e04_429_honors_retry_after_and_settles_each_attempt(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """429 按 Retry-After 退避重试：每次尝试各有租约与审计，远端应答的失败释放槽位。"""
    probe, _ = model_probe
    probe.retry_after_sec = 0.4
    probe.expect("429")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant, credentials=credentials, run_id=run_id, probe_url=pool_tenant.base_url
    )
    result = await _drive_runner(provider, deadline_ms=8000, retries=2)
    assert result.final_text == FINAL_TEXT

    assert len(probe.calls) == 2
    gap = probe.calls[1].arrived_at - probe.calls[0].arrived_at
    assert gap >= 0.4, f"429 必须遵守 Retry-After（实际间隔 {gap:.3f}s）"
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["SETTLED", "SETTLED"]
    assert leases[0]["reserved_tokens"] > 0 and leases[1]["reserved_tokens"] > 0
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [(audit["status"], audit["retry_reason"]) for audit in audits] == [
        ("RETRY", "RATE_LIMITED"),
        ("OK", None),
    ]
    # 失败不假定供方未计费：被 429 拒绝的那次尝试的 token 预占保守保留在窗口里
    inflight, reserved = await _capacity_totals(pool_tenant.tenant_id, leases)
    assert inflight == 0
    assert reserved == leases[0]["reserved_tokens"] + PROMPT_TOKENS + COMPLETION_TOKENS


async def test_e04_retry_after_beyond_deadline_stops_within_budget(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """Retry-After 超出剩余 deadline 时不再发起第二次调用（原 deadline 是硬预算）。"""
    from muad_agent_core.agent import RunnerDeadlineExceeded

    probe, _ = model_probe
    probe.retry_after_sec = 5.0
    probe.expect("429")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant, credentials=credentials, run_id=run_id, probe_url=pool_tenant.base_url
    )
    with pytest.raises(RunnerDeadlineExceeded):
        await _drive_runner(provider, deadline_ms=1000, retries=3)

    assert len(probe.calls) == 1
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["SETTLED"]
    inflight, reserved = await _capacity_totals(pool_tenant.tenant_id, leases)
    assert inflight == 0 and reserved > 0


# --------------------------------------------------------------------------- #
# E-04：5xx 有界重试 + UNKNOWN 保留占用
# --------------------------------------------------------------------------- #


async def test_e04_5xx_retries_are_bounded_and_occupancy_stays_unknown(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """5xx 在重试预算内处理：次数用尽即终止；无法确认远端结束的尝试保留 UNKNOWN 占用。"""
    from muad_agent_core.agent import RunnerModelError

    probe, _ = model_probe
    probe.expect("500", "500", "500", "500")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant, credentials=credentials, run_id=run_id, probe_url=pool_tenant.base_url
    )
    with pytest.raises(RunnerModelError):
        await _drive_runner(provider, deadline_ms=8000, retries=2)

    assert len(probe.calls) == 3, "重试预算 = max_model_retries（含首次共 3 次）"
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["UNKNOWN"] * 3
    inflight, reserved = await _capacity_totals(pool_tenant.tenant_id, leases)
    assert inflight == 3, "未知远端是否停止：不释放占用（不超发）"
    assert reserved == sum(int(lease["reserved_tokens"]) for lease in leases)
    audits = await _audit_rows(pool_tenant.tenant_id, run_id)
    assert [(audit["status"], audit["retry_reason"]) for audit in audits] == [
        ("RETRY", "UNAVAILABLE"),
        ("RETRY", "UNAVAILABLE"),
        ("RETRY", "UNAVAILABLE"),
    ]


# --------------------------------------------------------------------------- #
# E-04：断流不混拼另一轮回复
# --------------------------------------------------------------------------- #


async def test_e04_stream_break_terminates_without_mixing_another_round(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """断流（无收尾信号）按终止处理：不重试、不把另一 attempt 的回答拼到旧正文。"""
    from muad_agent_core.agent import RunnerModelError

    probe, _ = model_probe
    probe.expect("break")
    run_id = await _seed_run(pool_tenant)
    provider = await _pool_provider(
        pool_tenant, credentials=credentials, run_id=run_id, probe_url=pool_tenant.base_url
    )
    deltas: list[str] = []

    async def collect(text: str) -> None:
        deltas.append(text)

    with pytest.raises(RunnerModelError):
        await _drive_runner(provider, deadline_ms=8000, retries=3, on_delta=collect)

    assert deltas == ["partial-a", "partial-b"], "已输出正文只出现一次，不得重放"
    assert len(probe.calls) == 1, "断流后不得再发一次调用（否则会拼出另一轮回答）"
    leases = await _lease_rows(pool_tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["UNKNOWN"], "无收尾信号的流不构成远端完成证据"
    inflight, reserved = await _capacity_totals(pool_tenant.tenant_id, leases)
    assert inflight == 1 and reserved > 0


# --------------------------------------------------------------------------- #
# B-225：预占估算用冻结的 input_estimate_multiplier
# --------------------------------------------------------------------------- #


async def test_b225_reservation_estimate_scales_with_frozen_multiplier(
    pool_tenant: PoolTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
) -> None:
    """预占 = 保守输入估算 × 冻结倍率 + 实际发送的最大输出预算（design §3.7）。"""
    probe, _ = model_probe
    run_id = await _seed_run(pool_tenant)
    text = "pool estimate " * 8
    request = _model_request(text)
    reserved: dict[float, int] = {}
    for multiplier in (1.0, 4.0):
        provider = await _pool_provider(
            pool_tenant,
            credentials=credentials,
            run_id=run_id,
            probe_url=pool_tenant.base_url,
            multiplier=multiplier,
        )
        await provider.complete(request)
        leases = await _lease_rows(pool_tenant.tenant_id, run_id)
        reserved[multiplier] = int(leases[-1]["reserved_tokens"])

    assert reserved[1.0] >= 64 + 1, "预占必须覆盖本次实际发送的输出预算"
    assert reserved[4.0] - reserved[1.0] >= 3 * len(text.encode("utf-8")), (
        "倍率差异必须体现在预占上（估算含输入字节量）"
    )
    assert len(probe.calls) == 2
