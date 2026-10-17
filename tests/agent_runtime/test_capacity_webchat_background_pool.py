"""B-226 / E-06：摘要与记忆提炼共享模型容量（真实 PG + 真实模型 HTTP + 真实接线）。

真实边界（不 mock 存储、租约或模型协议）：

- 摘要：真实 `RunService.start` 建 Run（真实快照/受理/事件/终态）→ 真实
  `default_executor_factory` 装配 → 真实 `RuntimeContextCompactor` → 真实资源池
  （`ModelAllocator` + `ModelCapacityService`：预占 → 冻结引用解析 → 发上游 → 结算）；
- 记忆提炼：真实 `ConsolidationWorker`/`ConsolidationInvoker` 执行真实持久 job（隔离
  PostgreSQL 库 + 真实快照 + 真实 canonical 事件），模型调用同样经真实资源池；
- 真实 PostgreSQL：`runtime.model_call_lease`/`model_capacity`/`model_dispatch_state`/
  `model_invocation_audit`/`runtime_snapshot` 与 `control.model_*`（隔离租户，只写/删本租户行）；
- 真实 Console 应用（uvicorn 127.0.0.1 + 真实服务身份门控）提供 API-09 冻结引用解析（摘要路径）；
- 真实模型 HTTP 探针（uvicorn 127.0.0.1）：记录每次请求的型号/鉴权/正文/输出预算，按脚本返回 5xx。

关键断言（Acceptance Contract B-226 / E-06）：

- B-226：摘要与提炼的模型调用都走**同一物理资源预算**（同一 `runtime.model_capacity` 行按
  `key_resource_id` 合并计量，不按调用方拆开）；workload=`BACKGROUND`，归属分别是 Run 与
  memory job；摘要失败保留原历史、提炼失败整批回滚且主 Run 终态不变；
- E-06：摘要失败保留原历史；提炼失败整批回滚且主 Run 不变；无残留 ACTIVE 槽位（槽位泄漏）；
  退化 warning + metric 可见。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.context_settings import PoolExecutionPolicy
from muad_agent_runtime.application.executor import ExecutionDefaults
from muad_agent_runtime.application.memory_consolidation import ConsolidationInvoker
from muad_agent_runtime.application.memory_consolidation_effects import ConsolidationWorker
from muad_agent_runtime.application.model_capacity_service import ModelCapacityService
from muad_agent_runtime.application.run_service import snapshot_policy
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_dispatch_store import MemberRef
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_api import render_metrics
from muad_common import SharedSettings
from muad_contracts import ResolvedAgent, ResolveDefinitionResponse, ResolvedModel
from muad_contracts.model_pool import (
    ModelCallSettlement,
    ModelCapacityReservationRequest,
    ModelTokenUsage,
    RemoteCompletionProof,
    RunCallOwner,
    derive_model_key_resource_id,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

MAIN_MODEL_NAME = "gpt-4o-mini"
SUMMARY_MODEL_NAME = "cheap-summary-model"
#: 隔离库里的主模型型号（探针按型号区分响应形态：非主/摘要型号返回提炼批次 JSON）
CONSOLIDATION_MODEL_NAME = "isolated-main-model"
PROVIDER_KEY = "openai"
AGENT_KEY = "background-pool-agent"
MAIN_MAX_TOKENS = 64
SUMMARY_MAX_TOKENS = 32
SHARED_API_KEY = "sk-shared-physical-key"
MAIN_ANSWER = "收到，我接着聊。"
MAIN_PROMPT_TOKENS = 11
MAIN_COMPLETION_TOKENS = 7
SUMMARY_PROMPT_TOKENS = 23
SUMMARY_COMPLETION_TOKENS = 5
CONSOLIDATION_PROMPT_TOKENS = 31
CONSOLIDATION_COMPLETION_TOKENS = 9
#: 合并计量对照用的主调用实际用量（真实 settle 写入窗口）
RUN_CALL_PROMPT_TOKENS = 13
RUN_CALL_COMPLETION_TOKENS = 3
SUMMARY_MARKER = "[历史摘要]"
TURN_ONE_TEXT = "第一轮：先说一句"
TURN_TWO_TEXT = "第二轮：接着聊"
#: 摘要模型定义里的 endpoint 指向不可达域名：真正发出去的必须是**冻结成员凭据**的 base_url
UNREACHABLE_SUMMARY_BASE_URL = "https://summary.invalid/v1"

SUMMARY_JSON = json.dumps(
    {
        "user_goal": "把长会话压成摘要",
        "constraints": [],
        "progress": [],
        "open_items": [],
        "artifacts": [],
    },
    ensure_ascii=False,
)
CONSOLIDATION_JSON = json.dumps(
    {
        "candidates": [
            {"memory_key": "infer.color", "category": "PREFERENCE", "content": {"value": "blue"}}
        ],
        "task_completed": False,
    }
)


def _service_token() -> str:
    token = SharedSettings().internal_service_token
    assert token, "INTERNAL_SERVICE_TOKEN must be configured for the internal boundary"
    return token


# --------------------------------------------------------------------------- #
# 真实 HTTP 服务：模型探针 + 真实 Console 应用（API-09）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ProbeCall:
    """探针收到的一次真实模型请求（型号/鉴权/正文/输出预算/到达时刻）。"""

    model: str
    authorization: str
    messages: list[Any]
    max_tokens: int | None
    arrived_at: float


class ModelProbe:
    """真实模型 HTTP 探针：逐请求记录，并按**型号脚本**决定响应形态。"""

    def __init__(self) -> None:
        self.calls: list[ProbeCall] = []
        self.modes: dict[str, list[str]] = {}

    def expect(self, model: str, *modes: str) -> None:
        self.modes.setdefault(model, []).extend(modes)

    def _mode(self, model: str) -> str:
        script = self.modes.get(model)
        return script.pop(0) if script else "ok"

    def calls_for(self, model: str) -> list[ProbeCall]:
        return [call for call in self.calls if call.model == model]

    @property
    def authorization_keys(self) -> list[str]:
        return [call.authorization.removeprefix("Bearer ") for call in self.calls]


def _probe_app(probe: ModelProbe) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        body = await request.json()
        model = str(body.get("model"))
        probe.calls.append(
            ProbeCall(
                model=model,
                authorization=request.headers.get("authorization", ""),
                messages=list(body.get("messages") or []),
                max_tokens=body.get("max_tokens"),
                arrived_at=time.monotonic(),
            )
        )
        if probe._mode(model) == "500":
            return JSONResponse({"error": {"message": "probe failure"}}, status_code=500)
        if model == SUMMARY_MODEL_NAME:
            content, prompt, completion = SUMMARY_JSON, SUMMARY_PROMPT_TOKENS, SUMMARY_COMPLETION_TOKENS
        elif model == MAIN_MODEL_NAME:
            content, prompt, completion = MAIN_ANSWER, MAIN_PROMPT_TOKENS, MAIN_COMPLETION_TOKENS
        else:
            content, prompt, completion = (
                CONSOLIDATION_JSON,
                CONSOLIDATION_PROMPT_TOKENS,
                CONSOLIDATION_COMPLETION_TOKENS,
            )
        return JSONResponse(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": content}}
                ],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
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
# 真实事实读取（证据）
# --------------------------------------------------------------------------- #


async def _lease_rows(tenant_id: str, *, run_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    where = "tenant_id = :tenant_id" + ("" if run_id is None else " AND run_id = :run_id")
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, deployment_id, credential_id, key_resource_id,"
                    " workload, lease_owner, state, reserved_tokens, run_id, memory_job_id"
                    " FROM runtime.model_call_lease WHERE " + where + " ORDER BY create_time"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _capacity_row(tenant_id: str, key_resource_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = 'KEY' AND resource_id = :id"
                ).bindparams(tenant_id=tenant_id, id=key_resource_id)
            )
        ).mappings().one_or_none()
    assert row is not None, "同一物理 Key 必须有且只有一行容量"
    return dict(row)


async def _capacity_row_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text("SELECT count(*) FROM runtime.model_capacity WHERE tenant_id = :tenant_id")
                .bindparams(tenant_id=tenant_id)
            )
        )


async def _audit_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT attempt, model, status, input_tokens, output_tokens"
                    " FROM runtime.model_invocation_audit"
                    " WHERE tenant_id = :tenant_id AND run_id = :run_id ORDER BY attempt, create_time"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _dispatch_counts(tenant_id: str) -> dict[uuid.UUID, int]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT model_id, dispatch_count FROM runtime.model_dispatch_state"
                    " WHERE tenant_id = :tenant_id"
                ).bindparams(tenant_id=tenant_id)
            )
        ).mappings().all()
    return {row["model_id"]: int(row["dispatch_count"]) for row in rows}


async def _run_status(tenant_id: str, run_id: uuid.UUID) -> str:
    async with get_session_factory()() as session:
        status = await session.scalar(
            sa.text(
                "SELECT status FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :run_id"
            ).bindparams(tenant_id=tenant_id, run_id=run_id)
        )
    assert status is not None
    return str(status)


async def _event_types(tenant_id: str, run_id: uuid.UUID) -> list[str]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT event_type FROM runtime.canonical_event"
                    " WHERE tenant_id = :tenant_id AND run_id = :run_id ORDER BY seq"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).scalars().all()
    return [str(row) for row in rows]


def _metric_value(text: str, name: str, labels: dict[str, str]) -> float:
    """从真实 `/metrics` 渲染文本里取一个样本值（无该样本按 0）。"""
    wanted = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        head, _, value = line.rpartition(" ")
        if not head.endswith("{" + wanted + "}"):
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return 0.0


def _model_messages(call: ProbeCall) -> list[str]:
    return [json.dumps(message, ensure_ascii=False) for message in call.messages]


# --------------------------------------------------------------------------- #
# 摘要路径：真实 Run 驱动（真实 RunService + 真实 default_executor_factory）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BackgroundTenant:
    """隔离租户：主模型与摘要模型各一条定义，**共用同一把物理 Key**。"""

    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    main_model_id: uuid.UUID
    summary_model_id: uuid.UUID
    credential_id: uuid.UUID
    main_deployment_id: uuid.UUID
    summary_deployment_id: uuid.UUID
    api_key: str
    digest: str

    @property
    def key_resource_id(self) -> uuid.UUID:
        return derive_model_key_resource_id(self.tenant_id, PROVIDER_KEY, self.digest)


async def _insert_definition(
    session: Any,
    *,
    tenant_id: str,
    model_id: uuid.UUID,
    name: str,
    provider_model: str,
    max_tokens: int,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, provider_key,"
            " params_json) VALUES (:id, :tenant_id, :key, :name, :provider_model, :provider_key,"
            " CAST(:params AS jsonb))"
        ).bindparams(
            id=model_id,
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4().hex[:10]}",
            name=name,
            provider_model=provider_model,
            provider_key=PROVIDER_KEY,
            params=json.dumps({"temperature": 0.0, "max_tokens": max_tokens}),
        )
    )


async def _insert_credential(
    session: Any, *, tenant_id: str, credential_id: uuid.UUID, base_url: str, digest: str
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
            " api_key, api_key_digest, max_inflight, enabled, revision)"
            " VALUES (:id, :tenant_id, :key, :provider_key, :base_url, :api_key, :digest, 4,"
            " true, 1)"
        ).bindparams(
            id=credential_id,
            tenant_id=tenant_id,
            key=f"key-{uuid.uuid4().hex[:10]}",
            provider_key=PROVIDER_KEY,
            base_url=base_url,
            api_key=SHARED_API_KEY,
            digest=digest,
        )
    )


async def _insert_deployment(
    session: Any, *, tenant_id: str, model_id: uuid.UUID, credential_id: uuid.UUID
) -> uuid.UUID:
    deployment_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id, weight,"
            " enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, 1, true)"
        ).bindparams(
            id=deployment_id,
            tenant_id=tenant_id,
            key=f"member-{uuid.uuid4().hex[:10]}",
            model_id=model_id,
            credential_id=credential_id,
        )
    )
    return deployment_id


async def _seed_background_tenant(probe_url: str) -> BackgroundTenant:
    """真实控制面行：两条模型定义 + **一行**凭据（同一物理 Key）+ 两个成员关系。"""
    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    main_model_id, summary_model_id, credential_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(SHARED_API_KEY.encode()).hexdigest()
    async with get_session_factory()() as session:
        await _insert_definition(
            session,
            tenant_id=tenant_id,
            model_id=main_model_id,
            name="main-model",
            provider_model=MAIN_MODEL_NAME,
            max_tokens=MAIN_MAX_TOKENS,
        )
        await _insert_definition(
            session,
            tenant_id=tenant_id,
            model_id=summary_model_id,
            name="summary-model",
            provider_model=SUMMARY_MODEL_NAME,
            max_tokens=SUMMARY_MAX_TOKENS,
        )
        await _insert_credential(
            session,
            tenant_id=tenant_id,
            credential_id=credential_id,
            base_url=f"{probe_url}/v1",
            digest=digest,
        )
        main_deployment_id = await _insert_deployment(
            session, tenant_id=tenant_id, model_id=main_model_id, credential_id=credential_id
        )
        summary_deployment_id = await _insert_deployment(
            session, tenant_id=tenant_id, model_id=summary_model_id, credential_id=credential_id
        )
        await session.commit()
    return BackgroundTenant(
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
        main_model_id=main_model_id,
        summary_model_id=summary_model_id,
        credential_id=credential_id,
        main_deployment_id=main_deployment_id,
        summary_deployment_id=summary_deployment_id,
        api_key=SHARED_API_KEY,
        digest=digest,
    )


@pytest.fixture
async def background_tenant(
    model_probe: tuple[ModelProbe, str], tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[BackgroundTenant]:
    """每用例独立租户 + 独立产物根；结束只删本租户行，不动共享库其它数据。"""
    _, probe_url = model_probe
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    tenant = await _seed_background_tenant(probe_url)
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
                "DELETE FROM runtime.artifact WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.canonical_event WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_submission WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


class ObservingCredentialsClient:
    """真实 API-09 客户端的观察层：逐调用直通真实 HTTP，只记录解析载荷。"""

    def __init__(self, inner: ConsoleCredentialsClient) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._inner = inner

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = ""
    ) -> dict[str, Any]:
        self.payloads.append(dict(payload))
        return await self._inner.resolve_credentials(
            tenant_id=tenant_id, payload=payload, trace_id=trace_id
        )

    async def aclose(self) -> None:
        await self._inner.aclose()

    def frozen_payloads(self) -> list[dict[str, Any]]:
        return [payload for payload in self.payloads if payload.get("deployment_id")]


@pytest.fixture
async def credentials(console_url: str) -> AsyncIterator[ObservingCredentialsClient]:
    client = ObservingCredentialsClient(
        ConsoleCredentialsClient(console_url, service_token=_service_token())
    )
    try:
        yield client
    finally:
        await client.aclose()


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


def _resolved_definition(tenant: BackgroundTenant) -> ResolveDefinitionResponse:
    """主模型定义 + Agent 覆盖打开摘要层（`model_ref` 指向摘要模型定义）。"""
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key=AGENT_KEY,
            revision=1,
            instructions="You are the background pool agent.",
            runtime_config={
                "temperature": 0.0,
                "budget": {
                    "compaction": {
                        "summary": {
                            "enabled": True,
                            "threshold_bytes": 1,
                            "model_ref": str(tenant.summary_model_id),
                        }
                    }
                },
            },
        ),
        model=ResolvedModel(
            id=tenant.main_model_id,
            revision=1,
            model_id=MAIN_MODEL_NAME,
            base_url="https://main.invalid/v1",
            api_key="sk-interim-definition-key",
            params={"temperature": 0.0, "max_tokens": MAIN_MAX_TOKENS},
        ),
    )


@pytest.fixture
async def supervisor() -> AsyncIterator[Any]:
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor

    instance = ExecutionSupervisor()
    try:
        yield instance
    finally:
        await instance.close()


async def _start_run(
    tenant: BackgroundTenant,
    *,
    credentials: ObservingCredentialsClient,
    supervisor: Any,
    conversation_id: uuid.UUID | None = None,
    text: str = TURN_ONE_TEXT,
) -> uuid.UUID:
    """真实 RunService.start：真实快照/受理/执行装配（`default_executor_factory`）。"""
    from muad_agent_runtime.application.run_service import RunRequest, RunService
    from muad_contracts.runtime import ChannelContext, MessageInput

    from agent_runtime.conftest import FakeResolveClient

    resolve = FakeResolveClient(response=_resolved_definition(tenant))
    resolve.summary_models[tenant.summary_model_id] = ResolvedModel(
        id=tenant.summary_model_id,
        revision=1,
        model_id=SUMMARY_MODEL_NAME,
        base_url=UNREACHABLE_SUMMARY_BASE_URL,
        api_key=None,
        params={"temperature": 0.0, "max_tokens": SUMMARY_MAX_TOKENS},
    )
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
            "instance-b226",
            _PgSettingsClient(),
            credentials_client=credentials,
            supervisor=supervisor,
        ).start(request, tenant.tenant_id)
        async with asyncio.timeout(30):
            async for _ in started.events:
                pass
        return started.run_id


async def _conversation_of(tenant_id: str, run_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        conversation_id = await session.scalar(
            sa.text(
                "SELECT conversation_id FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(tenant_id=tenant_id, id=run_id)
        )
    assert conversation_id is not None
    return conversation_id


# --------------------------------------------------------------------------- #
# B-226：摘要走同一物理资源预算（BACKGROUND + 冻结引用解析 + 合并计量）
# --------------------------------------------------------------------------- #


async def test_b226_summary_call_uses_the_shared_pool_with_background_workload(
    background_tenant: BackgroundTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
    supervisor: Any,
) -> None:
    """第二轮触发摘要：摘要调用经资源池（BACKGROUND、冻结引用解析），与主调用合并计量。"""
    probe, _ = model_probe
    tenant = background_tenant
    first = await _start_run(tenant, credentials=credentials, supervisor=supervisor)
    conversation_id = await _conversation_of(tenant.tenant_id, first)
    second = await _start_run(
        tenant,
        credentials=credentials,
        supervisor=supervisor,
        conversation_id=conversation_id,
        text=TURN_TWO_TEXT,
    )
    assert await _run_status(tenant.tenant_id, second) == "COMPLETED"

    # 真实上游：摘要打到自己那条**冻结模型定义**的型号上，连接与凭据来自冻结成员（探针地址）
    summary_calls = probe.calls_for(SUMMARY_MODEL_NAME)
    assert len(summary_calls) == 1, "第二轮必须真的发起一次摘要调用"
    assert summary_calls[0].authorization == f"Bearer {tenant.api_key}"
    assert summary_calls[0].max_tokens == SUMMARY_MAX_TOKENS, (
        "摘要请求必须发送冻结定义的显式输出预算（池化预占的输入）"
    )
    main_calls = probe.calls_for(MAIN_MODEL_NAME)
    assert len(main_calls) == 2
    assert {call.authorization for call in main_calls} == {f"Bearer {tenant.api_key}"}
    assert any(TURN_TWO_TEXT in text for text in _model_messages(main_calls[1]))
    assert any(SUMMARY_MARKER in text for text in _model_messages(main_calls[1])), (
        "第二轮的主调用在摘要之后：摘要作为权威前缀进入请求"
    )

    # 摘要的凭据解析按**冻结三件套**（deployment/credential/expected_revision）走 API-09
    summary_frozen = [
        payload
        for payload in credentials.frozen_payloads()
        if str(payload["model_id"]) == str(tenant.summary_model_id)
    ]
    assert len(summary_frozen) == 1
    assert summary_frozen[0]["deployment_id"] == str(tenant.summary_deployment_id)
    assert summary_frozen[0]["credential_id"] == str(tenant.credential_id)
    assert summary_frozen[0]["expected_revision"] == 1
    assert summary_frozen[0]["execution_ref"] == {"type": "RUN", "id": str(second)}

    # 真实租约：摘要 = BACKGROUND，主调用 = INTERACTIVE，同一物理 Key 身份
    leases = await _lease_rows(tenant.tenant_id, run_id=second)
    assert sorted(lease["workload"] for lease in leases) == ["BACKGROUND", "INTERACTIVE"]
    assert {lease["state"] for lease in leases} == {"SETTLED"}
    assert {lease["key_resource_id"] for lease in leases} == {tenant.key_resource_id}
    assert {lease["lease_owner"] for lease in leases} == {"instance-b226"}

    # 合并计量：同一物理 Key 只有一行容量，两个调用方（主 + 摘要）的用量都在它上面
    capacity = await _capacity_row(tenant.tenant_id, tenant.key_resource_id)
    assert await _capacity_row_count(tenant.tenant_id) == 1, "同一实际 Key 不得拆成多行计量"
    assert capacity["inflight"] == 0
    assert capacity["reserved_requests"] == 3, "两轮主调用 + 一次摘要共用同一行窗口计数"
    assert capacity["reserved_tokens"] == (
        MAIN_PROMPT_TOKENS + MAIN_COMPLETION_TOKENS + SUMMARY_PROMPT_TOKENS + SUMMARY_COMPLETION_TOKENS
    ) + MAIN_PROMPT_TOKENS + MAIN_COMPLETION_TOKENS

    # 逐 attempt 审计可追溯：同一 Run 的摘要与主调用各有审计行、序号不重复
    audits = await _audit_rows(tenant.tenant_id, second)
    assert [(audit["attempt"], audit["model"], audit["status"]) for audit in audits] == [
        (0, SUMMARY_MODEL_NAME, "OK"),
        (1, MAIN_MODEL_NAME, "OK"),
    ]
    assert audits[0]["input_tokens"] == SUMMARY_PROMPT_TOKENS
    assert audits[0]["output_tokens"] == SUMMARY_COMPLETION_TOKENS

    # 调度计数：两条模型定义各被真实分配器计过尝试。共享 epoch 归一是 TASK-023 的既有语义：
    # 同一 60 秒 epoch 内累加、跨 epoch 只保留当前 epoch 的计数——摘要必然只有一次尝试，
    # 主模型两轮可能落在不同 epoch。
    counts = await _dispatch_counts(tenant.tenant_id)
    assert set(counts) == {tenant.main_model_id, tenant.summary_model_id}
    assert counts[tenant.summary_model_id] == 1
    assert 1 <= counts[tenant.main_model_id] <= 2

    # 摘要真的作为权威历史落库（持久事件）
    events = await _event_types(tenant.tenant_id, second)
    assert "CONTEXT_SUMMARY" in events and "CONTEXT_COMPACTED" in events


# --------------------------------------------------------------------------- #
# E-06：摘要失败保留原历史 + 退化可观测（无残留 ACTIVE 槽位）
# --------------------------------------------------------------------------- #


async def test_e06_summary_failure_keeps_original_history_and_visible_degradation(
    background_tenant: BackgroundTenant,
    model_probe: tuple[ModelProbe, str],
    credentials: ObservingCredentialsClient,
    supervisor: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """摘要模型 5xx：压缩退化为"不压缩"，原历史照发，warning + metric 可见，无残留 ACTIVE 槽位。"""
    probe, _ = model_probe
    tenant = background_tenant
    first = await _start_run(tenant, credentials=credentials, supervisor=supervisor)
    conversation_id = await _conversation_of(tenant.tenant_id, first)
    probe.expect(SUMMARY_MODEL_NAME, "500")
    before_metric = _metric_value(
        render_metrics(), "context_compaction_total", {"layer": "*", "status": "FAILED"}
    )
    with caplog.at_level(logging.WARNING):
        second = await _start_run(
            tenant,
            credentials=credentials,
            supervisor=supervisor,
            conversation_id=conversation_id,
            text=TURN_TWO_TEXT,
        )

    assert await _run_status(tenant.tenant_id, second) == "COMPLETED", "摘要失败不得让 Run 失败"
    main_calls = probe.calls_for(MAIN_MODEL_NAME)
    assert len(main_calls) == 2
    second_main = _model_messages(main_calls[1])
    assert not any(SUMMARY_MARKER in text for text in second_main), "失败摘要不得进入请求"
    assert any(TURN_ONE_TEXT in text for text in second_main), "原历史必须原样保留"
    assert (await _event_types(tenant.tenant_id, second)).count("CONTEXT_SUMMARY") == 0

    # 退化可见：warning + metric（不是悄悄吞掉）
    assert any(record.msg == "context_compaction_failed" for record in caplog.records)
    after_metric = _metric_value(
        render_metrics(), "context_compaction_total", {"layer": "*", "status": "FAILED"}
    )
    assert after_metric > before_metric

    # 无残留 ACTIVE 槽位；远端未确认结束的占用按 UNKNOWN 保守保留（设计语义，不是泄漏）
    leases = await _lease_rows(tenant.tenant_id, run_id=second)
    assert [lease["workload"] for lease in leases].count("BACKGROUND") == 1
    assert {lease["state"] for lease in leases} == {"UNKNOWN", "SETTLED"}
    capacity = await _capacity_row(tenant.tenant_id, tenant.key_resource_id)
    assert capacity["inflight"] == 1, "UNKNOWN 占用按设计保留（不等于槽位泄漏）"


# --------------------------------------------------------------------------- #
# 记忆提炼路径：隔离 PostgreSQL + 真实快照/事件 + 真实模型 HTTP
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FrozenMember:
    """隔离库里的真实成员事实（成员行 + 凭据行 + 所属模型定义）。"""

    model_id: uuid.UUID
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    api_key: str
    digest: str

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        return derive_model_key_resource_id(tenant_id, PROVIDER_KEY, self.digest)


class FrozenRefCredentials:
    """隔离库的 API-09 端口：冻结三件套给齐时返回完整 `SelectedModelCredential` 投影。

    缺省（过渡解析：canary 口径与 Run 创建边界一致）返回模型级首个可用成员的旧投影——
    与真实 Console 的两条分支同形。
    """

    def __init__(self, member: FrozenMember, *, base_url: str) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._member, self._base_url = member, base_url

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = ""
    ) -> dict[str, Any]:
        self.payloads.append(dict(payload))
        model_id = str(payload["model_id"])
        if payload.get("deployment_id") is None:
            return {"model": {"id": model_id, "api_key": self._member.api_key}, "mcp_servers": []}
        return {
            "model": {
                "model_id": model_id,
                "deployment_id": str(self._member.deployment_id),
                "credential_id": str(self._member.credential_id),
                "credential_revision": int(payload["expected_revision"]),
                "key_resource_id": str(self._member.key_resource_id(tenant_id)),
                "quota_group_id": None,
                "provider_key": PROVIDER_KEY,
                "base_url": self._base_url,
                "api_key": self._member.api_key,
            },
            "mcp_servers": [],
        }

    def frozen_payloads(self) -> list[dict[str, Any]]:
        return [payload for payload in self.payloads if payload.get("deployment_id")]


@dataclass(slots=True)
class ConsolidationStack:
    factory: Any
    tenant_id: str
    run: RunRecord
    job: Any
    member: FrozenMember
    credentials: FrozenRefCredentials
    worker: ConsolidationWorker


async def _seed_isolated_control(factory: Any, *, probe_url: str, tenant_id: str) -> FrozenMember:
    """隔离库里的真实控制面行：主模型定义 + 一行凭据 + 一个成员关系。"""
    model_id, credential_id = uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(SHARED_API_KEY.encode()).hexdigest()
    async with factory.begin() as session:
        await _insert_definition(
            session,
            tenant_id=tenant_id,
            model_id=model_id,
            name="isolated-main-model",
            provider_model=CONSOLIDATION_MODEL_NAME,
            max_tokens=MAIN_MAX_TOKENS,
        )
        await _insert_credential(
            session,
            tenant_id=tenant_id,
            credential_id=credential_id,
            base_url=f"{probe_url}/v1",
            digest=digest,
        )
        deployment_id = await _insert_deployment(
            session, tenant_id=tenant_id, model_id=model_id, credential_id=credential_id
        )
    return FrozenMember(
        model_id=model_id,
        deployment_id=deployment_id,
        credential_id=credential_id,
        api_key=SHARED_API_KEY,
        digest=digest,
    )


async def _freeze_members(
    factory: Any,
    run: RunRecord,
    *,
    member: FrozenMember,
    policy: MemoryPolicySettings,
    probe_url: str,
    pool: PoolExecutionPolicy | None = None,
) -> None:
    """把成员非密钥引用冻进真实快照（与 Run 创建边界同一 `snapshot_policy` 口径）。"""
    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        snapshot = await session.get(RuntimeSnapshot, row.snapshot_id)
        snapshot.policy_json = snapshot_policy(
            execution=ExecutionDefaults(memory_policy=policy),
            pool=pool,
            members=(
                MemberRef(
                    deployment_id=member.deployment_id,
                    credential_id=member.credential_id,
                    weight=1,
                ),
            ),
        )
        snapshot.model_json = {
            **snapshot.model_json,
            "id": str(member.model_id),
            "model_id": CONSOLIDATION_MODEL_NAME,
            "base_url": f"{probe_url}/v1",
            "params": {"temperature": 0.0, "max_tokens": MAIN_MAX_TOKENS},
        }


def _job_claim(job: Any) -> Any:
    from muad_agent_runtime.application.memory_jobs import JobClaim

    return JobClaim(
        id=job.id,
        tenant_id=job.tenant_id,
        run_id=job.run_id,
        conversation_id=job.conversation_id,
        owner=job.lease_owner,
        epoch=job.claim_epoch,
    )


async def _completed_job(factory: Any, run: RunRecord) -> Any:
    """把来源 Run 置为 COMPLETED 并给出一个真实可执行（RUNNING + 有效租约）的提炼 job。"""
    from muad_agent_runtime.infrastructure.models.runtime import MemoryConsolidationJob

    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        row.status = "COMPLETED"
        row.deadline_at = datetime.now(UTC) - timedelta(days=1)
        job = MemoryConsolidationJob(
            id=uuid.uuid4(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            snapshot_id=row.snapshot_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            through_seq=1,
            enqueued_scope_revision=0,
            claim_epoch=1,
            attempt=1,
            status="RUNNING",
            lease_owner="consolidation-owner",
            lease_until=datetime.now(UTC) + timedelta(seconds=60),
        )
        session.add(job)
    return job


@asynccontextmanager
async def _consolidation_stack(
    engine: AsyncEngine,
    *,
    probe_url: str,
    policy: MemoryPolicySettings,
    pool: PoolExecutionPolicy | None = None,
) -> AsyncIterator[ConsolidationStack]:
    """隔离库 + 真实来源 Run/快照/事件 + 真实 worker（真实资源池链路）。"""
    from tests.agent_runtime.memory_confirmation_support import source

    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    factory, run, _, _ = await source(engine, tenant=tenant_id)
    member = await _seed_isolated_control(factory, probe_url=probe_url, tenant_id=tenant_id)
    await _freeze_members(
        factory, run, member=member, policy=policy, probe_url=probe_url, pool=pool
    )
    job = await _completed_job(factory, run)
    credentials = FrozenRefCredentials(member, base_url=f"{probe_url}/v1")
    worker = ConsolidationWorker(factory, ConsolidationInvoker(factory, credentials))
    yield ConsolidationStack(
        factory=factory,
        tenant_id=tenant_id,
        run=run,
        job=job,
        member=member,
        credentials=credentials,
        worker=worker,
    )


async def _isolated_leases(factory: Any, tenant_id: str) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, workload, lease_owner, epoch, state, reserved_tokens,"
                    " key_resource_id, run_id, memory_job_id"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id ORDER BY create_time"
                ).bindparams(tenant_id=tenant_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _isolated_capacity(
    factory: Any, tenant_id: str, key_resource_id: uuid.UUID
) -> dict[str, Any]:
    async with factory() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = 'KEY' AND resource_id = :id"
                ).bindparams(tenant_id=tenant_id, id=key_resource_id)
            )
        ).mappings().one_or_none()
    assert row is not None, "同一物理 Key 必须有且只有一行容量"
    return dict(row)


async def _memory_row_count(factory: Any, tenant_id: str) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                sa.text("SELECT count(*) FROM runtime.user_memory WHERE tenant_id = :tenant_id")
                .bindparams(tenant_id=tenant_id)
            )
        )


async def _scope_revision(factory: Any, tenant_id: str) -> int:
    """记忆 scope 的 revision：失败批次不得推进它（旧缓存按 revision 失效，不留脏键）。"""
    async with factory() as session:
        return int(
            await session.scalar(
                sa.text("SELECT revision FROM runtime.memory_scope WHERE tenant_id = :tenant_id")
                .bindparams(tenant_id=tenant_id)
            )
            or 0
        )


async def _job_status(factory: Any, job_id: uuid.UUID) -> tuple[str, str | None]:
    async with factory() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, error_code FROM runtime.memory_consolidation_job WHERE id = :id"
                ).bindparams(id=job_id)
            )
        ).mappings().one()
    return str(row["status"]), row["error_code"]


async def _run_owned_reservation(
    factory: Any, *, tenant_id: str, run_id: uuid.UUID, member: FrozenMember, reserved_tokens: int
) -> None:
    """同一物理 Key 上的**主调用**（真实容量端口 + 真实 Run 归属）：合并计量的对照。"""
    service = ModelCapacityService(lambda: factory)
    reservation = await service.reserve(
        tenant_id=tenant_id,
        lease_owner="instance-b226",
        request=ModelCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=member.model_id,
            deployment_id=member.deployment_id,
            credential_id=member.credential_id,
            credential_revision=1,
            key_resource_id=member.key_resource_id(tenant_id),
            quota_group_id=None,
            owner=RunCallOwner(run_id=run_id),
            workload="INTERACTIVE",
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            reserved_tokens=reserved_tokens,
        ),
    )
    await service.settle(
        tenant_id=tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=ModelCallSettlement(
            epoch=reservation.epoch,
            lease_owner=reservation.lease_owner,
            outcome="SUCCEEDED",
            usage=ModelTokenUsage(
                input_tokens=RUN_CALL_PROMPT_TOKENS, output_tokens=RUN_CALL_COMPLETION_TOKENS
            ),
            remote_completion=RemoteCompletionProof(
                kind="PROVIDER_CONFIRMED", reference="fixture", completed_at=datetime.now(UTC)
            ),
        ),
    )


async def _hold_run_call(
    factory: Any, *, tenant_id: str, run_id: uuid.UUID, member: FrozenMember, reserved_tokens: int
) -> None:
    """在同一物理 Key 上留一条**在飞**的主调用租约（交互占着容量，后台必须让路）。"""
    service = ModelCapacityService(lambda: factory)
    await service.reserve(
        tenant_id=tenant_id,
        lease_owner="instance-b226",
        request=ModelCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=member.model_id,
            deployment_id=member.deployment_id,
            credential_id=member.credential_id,
            credential_revision=1,
            key_resource_id=member.key_resource_id(tenant_id),
            quota_group_id=None,
            owner=RunCallOwner(run_id=run_id),
            workload="INTERACTIVE",
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            reserved_tokens=reserved_tokens,
        ),
    )


# --------------------------------------------------------------------------- #
# B-226：提炼走同一物理资源预算（BACKGROUND + memory job 归属 + 合并计量）
# --------------------------------------------------------------------------- #


async def test_b226_consolidation_call_uses_the_shared_pool_and_merges_metering(
    async_tool_database: AsyncEngine,
    model_probe: tuple[ModelProbe, str],
) -> None:
    """真实提炼 job：模型调用经资源池（BACKGROUND、memory job 归属、冻结引用解析）并合并计量。"""
    probe, probe_url = model_probe
    policy = MemoryPolicySettings()
    async with _consolidation_stack(
        async_tool_database, probe_url=probe_url, policy=policy
    ) as stack:
        await _run_owned_reservation(
            stack.factory,
            tenant_id=stack.tenant_id,
            run_id=stack.run.id,
            member=stack.member,
            reserved_tokens=100,
        )
        result = await stack.worker.execute(_job_claim(stack.job))

        assert result is not None, "提炼必须真的完成并返回持久效果"
        calls = probe.calls_for(CONSOLIDATION_MODEL_NAME)
        assert len(calls) == 1, "提炼调用必须打到真实模型 HTTP"
        assert calls[0].authorization == f"Bearer {stack.member.api_key}"
        assert calls[0].max_tokens == policy.consolidation_max_output_tokens

        # 冻结引用解析：调用按快照冻结的成员三件套走 API-09（不是过渡解析）
        frozen = stack.credentials.frozen_payloads()
        assert len(frozen) == 1
        assert frozen[0]["deployment_id"] == str(stack.member.deployment_id)
        assert frozen[0]["credential_id"] == str(stack.member.credential_id)
        assert frozen[0]["expected_revision"] == 1
        assert frozen[0]["execution_ref"] == {"type": "RUN", "id": str(stack.run.id)}

        # 真实租约：BACKGROUND + memory job 归属，按实际 usage 结算
        leases = await _isolated_leases(stack.factory, stack.tenant_id)
        assert len(leases) == 2, "主调用预占 + 提炼调用各一条租约"
        job_leases = [lease for lease in leases if lease["memory_job_id"] is not None]
        assert len(job_leases) == 1
        job_lease = job_leases[0]
        assert job_lease["workload"] == "BACKGROUND"
        assert job_lease["state"] == "SETTLED"
        assert job_lease["epoch"] == 1, "首次尝试的租约代次（续租/接管才推进）"
        assert job_lease["run_id"] is None
        assert job_lease["lease_owner"] == "consolidation-owner"
        assert job_lease["key_resource_id"] == stack.member.key_resource_id(stack.tenant_id)

        # 合并计量：同一物理 Key 一行容量，主调用与提炼的窗口计数/用量都在它上面
        capacity = await _isolated_capacity(
            stack.factory, stack.tenant_id, stack.member.key_resource_id(stack.tenant_id)
        )
        assert capacity["inflight"] == 0, "两条调用都按完成证据释放了槽位"
        assert capacity["reserved_requests"] == 2, "主调用 + 提炼共用同一行窗口请求计数"
        assert capacity["reserved_tokens"] == (
            RUN_CALL_PROMPT_TOKENS + RUN_CALL_COMPLETION_TOKENS
        ) + (CONSOLIDATION_PROMPT_TOKENS + CONSOLIDATION_COMPLETION_TOKENS)

        # 提炼效果落库、主 Run 终态不变
        assert await _memory_row_count(stack.factory, stack.tenant_id) == 1
        assert await _job_status(stack.factory, stack.job.id) == ("DONE", None)
        async with stack.factory() as session:
            assert (await session.get(RunRecord, stack.run.id)).status == "COMPLETED"


# --------------------------------------------------------------------------- #
# B-226：后台让路与老化（不占额度、不永久饿死）
# --------------------------------------------------------------------------- #


def _pool_policy(*, aging_interval_sec: int, model_wait_timeout_sec: int) -> PoolExecutionPolicy:
    return PoolExecutionPolicy(
        dispatch_epoch_sec=60,
        input_estimate_multiplier=1.25,
        interactive_weight=8,
        background_weight=1,
        aging_interval_sec=aging_interval_sec,
        model_wait_timeout_sec=model_wait_timeout_sec,
    )


async def test_b226_background_call_yields_without_reserving_when_interactive_is_active(
    async_tool_database: AsyncEngine,
    model_probe: tuple[ModelProbe, str],
) -> None:
    """交互占着同一物理 Key 时后台让路：冻结等待预算内不占额度、不发上游，按回压失败可重试。"""
    probe, probe_url = model_probe
    pool = _pool_policy(aging_interval_sec=30, model_wait_timeout_sec=1)
    async with _consolidation_stack(
        async_tool_database, probe_url=probe_url, policy=MemoryPolicySettings(), pool=pool
    ) as stack:
        await _hold_run_call(
            stack.factory,
            tenant_id=stack.tenant_id,
            run_id=stack.run.id,
            member=stack.member,
            reserved_tokens=100,
        )
        started = time.monotonic()
        result = await stack.worker.execute(_job_claim(stack.job))
        elapsed = time.monotonic() - started

        assert result is None
        assert probe.calls_for(CONSOLIDATION_MODEL_NAME) == [], "让路期间不得发上游调用"
        assert elapsed < 10, f"等待必须被冻结预算截断（实际 {elapsed:.1f}s）"
        assert await _job_status(stack.factory, stack.job.id) == ("RETRY", "FAILED")

        leases = await _isolated_leases(stack.factory, stack.tenant_id)
        assert len(leases) == 1, "让路的后台尝试不得留下自己的租约（不占额度）"
        assert leases[0]["memory_job_id"] is None
        capacity = await _isolated_capacity(
            stack.factory, stack.tenant_id, stack.member.key_resource_id(stack.tenant_id)
        )
        assert capacity["reserved_requests"] == 1, "只有交互那次预占进了窗口"


async def test_b226_background_call_proceeds_after_aging_while_interactive_is_active(
    async_tool_database: AsyncEngine,
    model_probe: tuple[ModelProbe, str],
) -> None:
    """真实等待超过老化间隔后后台必须可进展：不永久饿死，调用照常经池完成。"""
    probe, probe_url = model_probe
    pool = _pool_policy(aging_interval_sec=1, model_wait_timeout_sec=10)
    async with _consolidation_stack(
        async_tool_database, probe_url=probe_url, policy=MemoryPolicySettings(), pool=pool
    ) as stack:
        await _hold_run_call(
            stack.factory,
            tenant_id=stack.tenant_id,
            run_id=stack.run.id,
            member=stack.member,
            reserved_tokens=100,
        )
        started = time.monotonic()
        result = await stack.worker.execute(_job_claim(stack.job))
        elapsed = time.monotonic() - started

        assert result is not None, "老化后后台必须真的完成"
        calls = probe.calls_for(CONSOLIDATION_MODEL_NAME)
        assert len(calls) == 1
        assert elapsed >= 0.9, f"让路至少持续到老化间隔（实际 {elapsed:.2f}s）"
        assert calls[0].arrived_at - started >= 0.9
        assert await _job_status(stack.factory, stack.job.id) == ("DONE", None)
        capacity = await _isolated_capacity(
            stack.factory, stack.tenant_id, stack.member.key_resource_id(stack.tenant_id)
        )
        assert capacity["reserved_requests"] == 2
        assert capacity["inflight"] == 1, "交互那次仍在飞"


# --------------------------------------------------------------------------- #
# E-06：提炼失败整批回滚 + 主 Run 不变 + 无残留槽位
# --------------------------------------------------------------------------- #


async def test_e06_consolidation_failure_rolls_back_batch_and_keeps_run_completed(
    async_tool_database: AsyncEngine,
    model_probe: tuple[ModelProbe, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """提炼模型 5xx：整批回滚（零记忆效果）、job 可重试、主 Run 终态不变、无残留 ACTIVE 槽位。"""
    probe, probe_url = model_probe
    probe.expect(CONSOLIDATION_MODEL_NAME, "500")
    before_metric = _metric_value(
        render_metrics(), "memory_consolidation_total", {"outcome": "ERROR"}
    )
    async with _consolidation_stack(
        async_tool_database, probe_url=probe_url, policy=MemoryPolicySettings()
    ) as stack:
        with caplog.at_level(logging.WARNING):
            result = await stack.worker.execute(_job_claim(stack.job))

        assert result is None, "失败不产生持久效果"
        assert await _memory_row_count(stack.factory, stack.tenant_id) == 0, "整批回滚，零记忆行"
        assert await _scope_revision(stack.factory, stack.tenant_id) == 0, (
            "失败批次不得推进 scope revision（旧记忆缓存按 revision 失效，不留脏键）"
        )
        status, error_code = await _job_status(stack.factory, stack.job.id)
        assert (status, error_code) == ("RETRY", "FAILED"), "失败留在 job 终态上，可重试"
        async with stack.factory() as session:
            assert (await session.get(RunRecord, stack.run.id)).status == "COMPLETED"

        leases = await _isolated_leases(stack.factory, stack.tenant_id)
        assert len(leases) == 1
        assert leases[0]["state"] == "UNKNOWN", "远端未确认结束：保留 UNKNOWN 占用"
        assert "ACTIVE" not in {lease["state"] for lease in leases}, "不得残留 ACTIVE 槽位"
        capacity = await _isolated_capacity(
            stack.factory, stack.tenant_id, stack.member.key_resource_id(stack.tenant_id)
        )
        assert capacity["inflight"] == 1, "UNKNOWN 占用按设计保留"

    assert any(record.msg == "memory_consolidation_failed" for record in caplog.records)
    after_metric = _metric_value(
        render_metrics(), "memory_consolidation_total", {"outcome": "ERROR"}
    )
    assert after_metric > before_metric
