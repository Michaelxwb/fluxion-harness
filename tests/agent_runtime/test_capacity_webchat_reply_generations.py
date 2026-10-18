"""B-262：部分正文失败后的模型重试边界（TASK-062；design §3.6 公开流与持久写入 / §3.3 数据设计）。

真实边界（不 mock 存储或租约）：

- **真实 Runtime 业务服务**：`RunService.start` 受理 → 真实 `default_executor_factory` →
  `PooledModelProvider`（真实分配器/容量服务/续租/结算）→ 真实 `AgentRunner` → 真实
  `AgentRunnerExecutor`/`ModelCallTrace`；装配只决定"模型端点在哪儿"与"成员是哪几个"，
  不替换存储、租约、执行器或分代账本；
- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + `alembic upgrade head`，跑完
  drop）与隔离 Redis 号位：会话/冻结快照/Run/canonical 事件/租约/审计/工具操作都是真实行，
  `seq` 由真实 `EventWriter` 在会话行锁内分配；
- **真实 Console 应用**（uvicorn 127.0.0.1 + 真实服务身份门控）提供 API-09 冻结引用解析：
  每次尝试的 Key 都是真实 `control.model_credential` 行的当前值；
- **真实 HTTP 模型探针**（uvicorn 127.0.0.1）：回合按脚本逐块下发正文；"断流"回合在正文之后
  **真实切断连接**（生成器抛错 ⇒ 传输层断流 ⇒ `ModelUnavailableError`，此时正文已外发）；
  探针逐请求记录型号/鉴权/消息/工具定义。

关键断言（Acceptance Contract B-262）：

1. **显式分代**：已产生部分正文后的可重试失败在重试回合显式开启新 `reply_generation`——新代先落
   一条持久 `message.snapshot`（整组替换语义，正文从此代重建），旧代正文不再增长、不重复；
2. **不同 Key/attempt 不能拼正文**：重试回合换到另一名成员（另一把 Key），两次尝试的正文绝不
   拼接；公开投影按代替换后，可见正文只等于新代内容（旧代被 fence）；
3. **策略选择终止**：重试预算耗尽（`agent.max_model_retries=0`）时不开启新代——Run 以
   `MODEL_UNAVAILABLE` 失败收尾，已确认的部分正文保持唯一一份，不伪造 snapshot；
4. **旧尝试迟到输出被 fence**：回合结束后的迟到输出、以及被放弃尝试的迟到输出，一律不进入任何
   一代正文（warning 留痕）；分代账本只在"已产生正文后失败"时递增；
5. **已提交工具业务键不被重试更换**：重试回合的请求上下文与失败回合逐字节一致（同一批消息/工具
   定义），不追加、不重放工具回合；重试再次命中原工具调用时，`(run_id, source_tool_call_id)`
   幂等身份返回**原操作**——业务键（operation id / `runtime-op:{id}:submit`）前后逐字节一致，
   不产生第二份操作。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from muad_agent_core.agent import AgentRunner
from muad_agent_core.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
)
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    AuditedModelProvider,
    ExecutorEvent,
    ExecutorRequest,
)
from muad_agent_runtime.application.model_call_trace import ModelCallTrace
from muad_agent_runtime.application.public_events import project_public_stream_event
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    RunRequest,
    RunStatus,
)
from muad_contracts.model_pool import derive_model_key_resource_id
from muad_contracts.platform_settings import AgentSettings, CapacitySettings, ModelPoolSettings
from muad_contracts.runtime import ChannelContext, MessageInput
from muad_contracts.webchat_events import (
    PublicWebchatEvent,
    WebchatDeltaEvent,
    WebchatSnapshotEvent,
)

from agent_runtime.conftest import FakeResolveClient

pytestmark = pytest.mark.integration

MODEL_NAME = "gpt-4o-mini"
PROVIDER_KEY = "openai"
AGENT_KEY = "reply-generations-agent"
SKILL_KEY = "async_reply_skill"
WAIT_TIMEOUT_SEC = 30.0
DELTA_STREAM_TYPE = "message.delta"
SNAPSHOT_STREAM_TYPE = "message.snapshot"
TERMINAL_STREAM_TYPES = ("run.completed", "run.failed")
#: 第一回合：已外发的部分正文（断流后不得重放、不得与新代拼接）。
OLD_PARTIAL_TEXT = "旧代已外发正文。"
#: 重试回合：新代的完整回答（可见正文只应等于它）。
NEW_GENERATION_TEXT = "新代完整回答。"


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL / Redis + 真实 Runtime/Console 进程环境
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededMember:
    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    api_key: str
    digest: str

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        return derive_model_key_resource_id(tenant_id, PROVIDER_KEY, self.digest)


@dataclass(frozen=True, slots=True)
class ReplyTenant:
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    model_id: uuid.UUID
    members: tuple[SeededMember, ...]
    skill: ResolvedSkill | None = None


@pytest.fixture
async def reply_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[ReplyTenant]:
    """隔离 PostgreSQL/Redis 号位 + Runtime/Console 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    from muad_console_platform.infrastructure.db import dispose_engine as dispose_console_engine

    await dispose_console_engine()
    try:
        yield ReplyTenant(
            tenant_id=f"replygen-{uuid.uuid4().hex[:12]}",
            agent_id=uuid.uuid4(),
            platform_user_id=uuid.uuid4(),
            model_id=uuid.uuid4(),
            members=(),
        )
    finally:
        await runtime_db.dispose_engine()
        await dispose_console_engine()


# --------------------------------------------------------------------------------------
# 真实 HTTP 模型探针：脚本化回合（完整 / 工具调用 / 断流），逐请求记录真实输入
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class ProbeCall:
    model: str
    authorization: str
    messages: list[Any]
    tools: list[Any]


@dataclass(frozen=True, slots=True)
class OkRound:
    """完整回合：逐块正文 + 收尾信号（`finish_reason` + `[DONE]`）。"""

    chunks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CutRound:
    """已外发部分正文后**真实切断连接**：没有收尾信号，传输层断流（可重试失败）。"""

    chunks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolCallRound:
    """工具回合：返回一个 `execute_skill` 调用（finish_reason=tool_calls）。"""

    call_id: str
    skill_key: str
    input_data: dict[str, Any]


class ReplyProbe:
    """真实模型探针：按脚本决定每个回合的响应形态，并记录真实请求。"""

    def __init__(self) -> None:
        self.script: list[OkRound | CutRound | ToolCallRound] = []
        self.calls: list[ProbeCall] = []
        #: 超过 `hold_after_calls` 个请求后先等闸门再响应（给测试读取中途事实的窗口）。
        self.hold_after_calls: int | None = None
        self.hold_gate = asyncio.Event()

    def expect(self, *rounds: OkRound | CutRound | ToolCallRound) -> None:
        self.script.extend(rounds)

    @property
    def authorization_keys(self) -> list[str]:
        return [call.authorization.removeprefix("Bearer ") for call in self.calls]

    async def _next_round(self) -> OkRound | CutRound | ToolCallRound:
        if self.hold_after_calls is not None and len(self.calls) > self.hold_after_calls:
            await self.hold_gate.wait()
        return self.script.pop(0) if self.script else OkRound(chunks=("probe-default",))


def _chunk_frame(text: str, *, finish_reason: str | None = None) -> str:
    choice: dict[str, Any] = {"index": 0, "delta": {"content": text} if text else {}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return "data: " + json.dumps({"choices": [choice]}) + "\n\n"


def _tool_call_frame(round_: ToolCallRound) -> str:
    delta = {
        "tool_calls": [
            {
                "index": 0,
                "id": round_.call_id,
                "type": "function",
                "function": {
                    "name": "execute_skill",
                    "arguments": json.dumps(
                        {"skill_key": round_.skill_key, "input": round_.input_data}
                    ),
                },
            }
        ]
    }
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]}) + "\n\n"


async def _round_frames(round_: OkRound | CutRound | ToolCallRound) -> AsyncIterator[str]:
    if isinstance(round_, ToolCallRound):
        yield _tool_call_frame(round_)
        yield _chunk_frame("", finish_reason="tool_calls")
        yield "data: [DONE]\n\n"
        return
    for text in round_.chunks:
        yield _chunk_frame(text)
    if isinstance(round_, CutRound):
        raise RuntimeError("probe cut: connection aborted after partial output")
    yield _chunk_frame("", finish_reason="stop")
    yield "data: [DONE]\n\n"


def _probe_app(probe: ReplyProbe) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> StreamingResponse:
        body = await request.json()
        probe.calls.append(
            ProbeCall(
                model=str(body.get("model")),
                authorization=request.headers.get("authorization", ""),
                messages=list(body.get("messages") or []),
                tools=list(body.get("tools") or []),
            )
        )
        return StreamingResponse(
            _round_frames(await probe._next_round()), media_type="text/event-stream"
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
async def console_url() -> AsyncIterator[str]:
    """真实 Console 应用（真实 API-09 路由 + 真实服务身份门控 + 隔离 PG）。"""
    from muad_console_platform.main import app as console_app

    async with _serve(console_app) as url:
        yield url


@pytest.fixture
async def credentials(console_url: str) -> AsyncIterator[ConsoleCredentialsClient]:
    token = SharedSettings().internal_service_token
    assert token, "INTERNAL_SERVICE_TOKEN must be configured for the internal boundary"
    client = ConsoleCredentialsClient(console_url, service_token=token)
    try:
        yield client
    finally:
        await client.aclose()


# --------------------------------------------------------------------------------------
# 控制面播种：真实模型定义 / 物理凭据 / 成员 + 平台设置 + 真实 Run
# --------------------------------------------------------------------------------------


def _async_skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key=SKILL_KEY,
        name="Async Reply Skill",
        description="",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key=f"skills/{SKILL_KEY}.zip",
        execution_mode="ASYNC",
    )


async def _seed_members(
    tenant: ReplyTenant, model_base_url: str, *, skill: ResolvedSkill | None = None
) -> ReplyTenant:
    """两名等权成员（真实 credential/deployment 行）：重试回合按调度事实换到另一把 Key。"""
    members: list[SeededMember] = []
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, provider_key,"
                " params_json) VALUES (:id, :tenant_id, :key, :name, :provider_model, :provider_key,"
                " CAST(:params AS jsonb))"
            ).bindparams(
                id=tenant.model_id,
                tenant_id=tenant.tenant_id,
                key=f"model-{uuid.uuid4().hex[:10]}",
                name="reply-model",
                provider_model=MODEL_NAME,
                provider_key=PROVIDER_KEY,
                params='{"temperature": 0.0, "max_tokens": 64}',
            )
        )
        for index in range(2):
            credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
            api_key = f"sk-reply-{index}-{uuid.uuid4().hex}"
            digest = hashlib.sha256(api_key.encode()).hexdigest()
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
                    " api_key, api_key_digest, max_inflight, enabled, revision)"
                    " VALUES (:id, :tenant_id, :key, :provider_key, :base_url, :api_key, :digest, 5,"
                    " true, 1)"
                ).bindparams(
                    id=credential_id,
                    tenant_id=tenant.tenant_id,
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
                    tenant_id=tenant.tenant_id,
                    key=f"member-{index}-{uuid.uuid4().hex[:10]}",
                    model_id=tenant.model_id,
                    credential_id=credential_id,
                )
            )
            members.append(
                SeededMember(
                    deployment_id=deployment_id,
                    credential_id=credential_id,
                    api_key=api_key,
                    digest=digest,
                )
            )
        await session.commit()
    return replace(tenant, members=tuple(members), skill=skill)


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
    tenant_id: str, *, agent: AgentSettings, capacity: CapacitySettings
) -> None:
    """真实 Console 设置服务写入真实 PG（模型池策略 + 等待预算 + 重试预算）。"""
    from muad_console_platform.application.audit_service import AuditActor
    from muad_console_platform.application.platform_settings_service import PlatformSettingsService

    async with get_session_factory()() as session:
        service = PlatformSettingsService(session)
        current = await service.read_current(tenant_id)
        await service.save(
            tenant_id,
            AuditActor(account_id=uuid.uuid4()),
            current.revision,
            replace(
                current.settings,
                agent=agent,
                capacity=capacity,
                model_pool=ModelPoolSettings(input_estimate_multiplier=1.0),
            ),
        )
        await session.commit()


def _resolved_definition(tenant: ReplyTenant, model_base_url: str) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id,
            key=AGENT_KEY,
            revision=1,
            instructions="You are the reply-generations agent.",
            runtime_config={},
        ),
        model=ResolvedModel(
            id=tenant.model_id,
            revision=1,
            model_id=MODEL_NAME,
            base_url=f"{model_base_url}/v1",
            api_key="sk-interim-definition-key",
            params={"temperature": 0.0, "max_tokens": 64},
        ),
        skills=[tenant.skill] if tenant.skill is not None else [],
    )


async def _start_run(
    tenant: ReplyTenant,
    *,
    model_base_url: str,
    credentials: ConsoleCredentialsClient,
    supervisor: Any,
    agent: AgentSettings,
) -> tuple[uuid.UUID, list[ExecutorEvent]]:
    """真实 RunService.start：真实快照/受理/池化执行装配，跑完整个执行片段。"""
    await _save_settings(
        tenant.tenant_id, agent=agent, capacity=CapacitySettings(model_wait_timeout_sec=1)
    )
    request = RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="请回答"),
    )
    async with get_session_factory()() as session:
        service = RunService(
            session,
            FakeResolveClient(response=_resolved_definition(tenant, model_base_url)),
            "instance-b262",
            _PgSettingsClient(),
            credentials_client=credentials,
            supervisor=supervisor,
        )
        start: RunStart = await service.start(request, tenant.tenant_id)
        events = [event async for event in start.events]
    return start.run_id, events


@pytest.fixture
async def supervisor() -> AsyncIterator[Any]:
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor

    instance = ExecutionSupervisor()
    try:
        yield instance
    finally:
        await instance.close()


# --------------------------------------------------------------------------------------
# 真实事实读取（证据）
# --------------------------------------------------------------------------------------


async def _canonical_rows(run_id: uuid.UUID) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(CanonicalEvent.run_id == run_id, CanonicalEvent.is_deleted.is_(False))
            .order_by(CanonicalEvent.seq)
        )
        return list(rows)


def _payload(row: CanonicalEvent) -> dict[str, Any]:
    return row.payload_json if isinstance(row.payload_json, dict) else {}


def _rows_of(rows: Sequence[CanonicalEvent], stream_type: str) -> list[CanonicalEvent]:
    return [row for row in rows if row.stream_type == stream_type]


def _delta_text(rows: Sequence[CanonicalEvent], generation: int) -> str:
    return "".join(
        str(_payload(row).get("delta", ""))
        for row in rows
        if row.stream_type == DELTA_STREAM_TYPE
        and _payload(row).get("reply_generation") == generation
    )


async def _lease_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, credential_id, key_resource_id, state, reserved_tokens"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id AND run_id = :run_id"
                    " ORDER BY create_time, call_attempt_id"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _audit_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT attempt, status, retry_reason FROM runtime.model_invocation_audit"
                    " WHERE tenant_id = :tenant_id AND run_id = :run_id ORDER BY attempt"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _operation_rows(tenant_id: str, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, source_tool_call_id, input_hash, status, submission_json"
                    " FROM runtime.tool_operation WHERE tenant_id = :tenant_id AND run_id = :run_id"
                    " ORDER BY create_time, id"
                ).bindparams(tenant_id=tenant_id, run_id=run_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def _run_row(run_id: uuid.UUID) -> RunRecord | None:
    async with get_session_factory()() as session:
        return await session.get(RunRecord, run_id)


def _visible_body(rows: Sequence[CanonicalEvent]) -> str:
    """公开投影的客户端应用语义：delta 追加、snapshot 整组替换（按代替换 = 旧代被 fence）。"""
    body = ""
    for row in rows:
        event: PublicWebchatEvent | None = project_public_stream_event(row, {})
        if isinstance(event, WebchatDeltaEvent):
            body += event.data.text
        elif isinstance(event, WebchatSnapshotEvent):
            body = event.data.text
    return body


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]] | Callable[[], bool],
    *,
    what: str,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"等待超时（{timeout}s）：{what}")


# --------------------------------------------------------------------------------------
# 场景 1：部分正文失败 → 重试显式分代（持久 snapshot 替换），不同 Key 不拼正文
# --------------------------------------------------------------------------------------


async def test_b262_partial_output_failure_retries_as_new_generation_with_snapshot(
    reply_tenant: ReplyTenant,
    credentials: ConsoleCredentialsClient,
    supervisor: Any,
) -> None:
    """第一回合外发部分正文后断流 ⇒ 重试回合换 Key 并以持久 snapshot 开启新代。"""
    probe = ReplyProbe()
    probe.expect(CutRound(chunks=(OLD_PARTIAL_TEXT,)), OkRound(chunks=(NEW_GENERATION_TEXT,)))
    async with _serve(_probe_app(probe)) as model_url:
        tenant = await _seed_members(reply_tenant, model_url)
        run_id, _events = await _start_run(
            tenant,
            model_base_url=model_url,
            credentials=credentials,
            supervisor=supervisor,
            agent=AgentSettings(max_model_retries=2),
        )

    rows = await _canonical_rows(run_id)
    deltas = _rows_of(rows, DELTA_STREAM_TYPE)
    snapshots = _rows_of(rows, SNAPSHOT_STREAM_TYPE)
    terminals = [row for row in rows if row.stream_type in TERMINAL_STREAM_TYPES]

    # ① 旧代正文只落一份（已确认字节不重复、不重放），新代正文只落新代
    assert _delta_text(rows, 0) == OLD_PARTIAL_TEXT, "已外发的部分正文必须唯一落库"
    assert _delta_text(rows, 1) == NEW_GENERATION_TEXT
    assert all(_payload(row).get("reply_generation") in (0, 1) for row in deltas), (
        "每行增量都显式携带生成代号"
    )
    assert all(isinstance(_payload(row).get("delta"), str) for row in deltas)

    # ② 新代以**持久 snapshot** 开启：整组替换语义，正文从此代重建
    assert len(snapshots) == 1, "显式分代必须在持久层留下唯一一条替换边界"
    snapshot = snapshots[0]
    assert _payload(snapshot) == {"reply_generation": 1, "text": ""}, (
        "替换边界属于新代：先以空正文整组替换旧代，再由 delta 继续构建"
    )
    first_new_delta = next(row for row in deltas if _payload(row).get("reply_generation") == 1)
    last_old_delta = next(
        row for row in reversed(deltas) if _payload(row).get("reply_generation") == 0
    )
    assert last_old_delta.seq < snapshot.seq < first_new_delta.seq, (
        "顺序：旧代正文 → 新代替换边界 → 新代正文"
    )

    # ③ 终态排在全部正文之后；终态正文与新代一致
    assert len(terminals) == 1 and terminals[0].stream_type == "run.completed"
    assert _payload(terminals[0]).get("status") == "COMPLETED"
    assert _payload(terminals[0]).get("final_text") == NEW_GENERATION_TEXT
    assert terminals[0].seq > max(row.seq for row in [*deltas, *snapshots])
    run = await _run_row(run_id)
    assert run is not None and run.status == RunStatus.COMPLETED

    # ④ 公开投影按代替换：可见正文只等于新代，旧代被 fence
    assert _visible_body(rows) == NEW_GENERATION_TEXT
    assert OLD_PARTIAL_TEXT not in _visible_body(rows)
    snapshot_event = project_public_stream_event(snapshot, {})
    assert isinstance(snapshot_event, WebchatSnapshotEvent)
    assert snapshot_event.data.reply_generation == 1 and snapshot_event.data.text == ""
    assert snapshot_event.seq == snapshot.seq

    # ⑤ 不同 Key/attempt：两次尝试各用一名成员（另一把 Key），型号与上下文一致
    assert len(probe.calls) == 2, "重试必须真的再发一次上游调用"
    sent_keys = probe.authorization_keys
    assert sent_keys[0] != sent_keys[1], "重试回合必须换到另一名成员（另一把 Key）"
    assert set(sent_keys) == {member.api_key for member in tenant.members}
    assert {call.model for call in probe.calls} == {MODEL_NAME}, "不跨型号暗换"
    assert probe.calls[0].messages == probe.calls[1].messages, (
        "重试不追加/不改写上下文（已提交工具业务键不被重试更换）"
    )
    assert probe.calls[0].tools == probe.calls[1].tools

    # ⑥ 租约与逐 attempt 审计：两次尝试各自结算，失败尝试保留 UNKNOWN 占用
    leases = await _lease_rows(tenant.tenant_id, run_id)
    assert [lease["state"] for lease in leases] == ["UNKNOWN", "SETTLED"]
    assert leases[0]["key_resource_id"] != leases[1]["key_resource_id"], "两次尝试用了不同的 Key 身份"
    audits = await _audit_rows(tenant.tenant_id, run_id)
    assert [(audit["attempt"], audit["status"], audit["retry_reason"]) for audit in audits] == [
        (0, "RETRY", "UNAVAILABLE"),
        (1, "OK", None),
    ]


# --------------------------------------------------------------------------------------
# 场景 2：重试预算耗尽 → 策略选择终止（不开启新代、不伪造 snapshot）
# --------------------------------------------------------------------------------------


async def test_b262_exhausted_retry_budget_terminates_without_new_generation(
    reply_tenant: ReplyTenant,
    credentials: ConsoleCredentialsClient,
    supervisor: Any,
) -> None:
    """`agent.max_model_retries=0`：已产生正文后的可重试失败直接终止，不开启新代。"""
    probe = ReplyProbe()
    probe.expect(CutRound(chunks=(OLD_PARTIAL_TEXT,)))
    async with _serve(_probe_app(probe)) as model_url:
        tenant = await _seed_members(reply_tenant, model_url)
        run_id, _events = await _start_run(
            tenant,
            model_base_url=model_url,
            credentials=credentials,
            supervisor=supervisor,
            agent=AgentSettings(max_model_retries=0),
        )

    rows = await _canonical_rows(run_id)
    deltas = _rows_of(rows, DELTA_STREAM_TYPE)
    terminals = [row for row in rows if row.stream_type in TERMINAL_STREAM_TYPES]
    assert _rows_of(rows, SNAPSHOT_STREAM_TYPE) == [], "终止分支不得伪造替换边界"
    assert _delta_text(rows, 0) == OLD_PARTIAL_TEXT, "终止前已确认正文保持唯一一份"
    assert all(_payload(row).get("reply_generation") == 0 for row in deltas)
    assert len(terminals) == 1 and terminals[0].stream_type == "run.failed"
    assert _payload(terminals[0]).get("error_code") == "MODEL_UNAVAILABLE"
    assert terminals[0].seq > max(row.seq for row in deltas)
    run = await _run_row(run_id)
    assert run is not None and run.status == RunStatus.FAILED
    assert len(probe.calls) == 1, "重试预算耗尽不得再发上游调用"
    assert _visible_body(rows) == OLD_PARTIAL_TEXT, "终止分支保留已确认的部分正文"


# --------------------------------------------------------------------------------------
# 场景 3：分代账本的边界语义（纯层证据，与集成场景互补）
# --------------------------------------------------------------------------------------


def test_b262_ledger_opens_new_generation_only_after_partial_output_failure() -> None:
    """只在"已产生正文后失败"时递增；未产生正文的失败与正常回合都不分代。"""
    trace = ModelCallTrace(audit_enabled=False)
    assert trace.reply_generation == 0
    first = trace.begin()
    assert (first.generation, first.supersedes) == (0, False)
    assert trace.accepts_output() is True, "回合内接受正文输出"
    trace.note_delta()
    trace.close_round()
    assert trace.accepts_output() is False, "回合结束后的迟到输出被 fence"

    trace.note_failure_after_output()
    second = trace.begin()
    assert (second.generation, second.supersedes) == (1, True), "已产生正文后失败 ⇒ 显式分代"
    trace.close_round()

    third = trace.begin()
    assert (third.generation, third.supersedes) == (1, False), "无正文失败的后续回合不再分代"
    trace.close_round()
    trace.note_failure_after_output()
    fourth = trace.begin()
    assert (fourth.generation, fourth.supersedes) == (1, False), "未产生正文的失败不分代"

    audit_trace = ModelCallTrace()
    assert "model_audit_id" in audit_trace.begin().event_fields
    assert ModelCallTrace(audit_enabled=False).begin().event_fields == {}, (
        "无审计关联的调用点不得在事件里冒充审计身份"
    )


# --------------------------------------------------------------------------------------
# 场景 4：旧尝试迟到输出被 fence（回合外与跨尝试两种形状）
# --------------------------------------------------------------------------------------


class _LateOutputProvider:
    """回合结束后才到达的迟到输出（真实回调形状；纯层围栏证据）。"""

    def __init__(self, *, late_text: str, late_gate: asyncio.Event, late_done: asyncio.Event) -> None:
        self._late_text = late_text
        self._late_gate = late_gate
        self._late_done = late_done
        self._callback: Callable[[str], Awaitable[None]] | None = None
        self._late_task: asyncio.Task[None] | None = None

    def _schedule_late(self) -> None:
        async def late() -> None:
            await self._late_gate.wait()
            callback = self._callback
            assert callback is not None
            await callback(self._late_text)
            self._late_done.set()

        self._late_task = asyncio.create_task(late())

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("streaming path expected")

    async def stream(
        self, request: ModelRequest, on_delta: Callable[[str], Awaitable[None]]
    ) -> ModelResponse:
        self._callback = on_delta
        await on_delta("回合内正文。")
        self._schedule_late()
        return ModelResponse(content="回合内正文。", finish_reason="stop")


class _AbandoningProvider:
    """第一回合外发正文后失败，并保留对旧回调的引用（被放弃尝试的迟到输出形状）。"""

    def __init__(self, *, late_text: str, late_gate: asyncio.Event, late_done: asyncio.Event) -> None:
        self._late_text = late_text
        self._late_gate = late_gate
        self._late_done = late_done
        self.attempts = 0
        self._late_task: asyncio.Task[None] | None = None

    def _schedule_late(self, callback: Callable[[str], Awaitable[None]]) -> None:
        async def late() -> None:
            await self._late_gate.wait()
            await callback(self._late_text)
            self._late_done.set()

        self._late_task = asyncio.create_task(late())

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("streaming path expected")

    async def stream(
        self, request: ModelRequest, on_delta: Callable[[str], Awaitable[None]]
    ) -> ModelResponse:
        self.attempts += 1
        if self.attempts == 1:
            await on_delta("第一尝试正文。")
            self._schedule_late(on_delta)
            raise ModelUnavailableError("probe cut after partial output")
        await on_delta("第二尝试正文。")
        return ModelResponse(content="第二尝试正文。", finish_reason="stop")


async def _drive_executor(
    provider: ModelProvider, *, trace: ModelCallTrace, text: str = "question"
) -> list[ExecutorEvent]:
    async def not_cancelled() -> bool:
        return False

    request = ExecutorRequest(
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="fence-agent", revision=1, instructions="", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            model_id="probe",
            revision=1,
            base_url="http://model.invalid/v1",
            params={},
        ),
        input_text=text,
        is_cancel_requested=not_cancelled,
    )
    runner = AgentRunner(provider=provider, registry=ToolRegistry())
    return [
        event
        async for event in AgentRunnerExecutor(
            runner=runner, request=request, model_trace=trace
        ).run()
    ]


async def test_b262_late_output_after_round_close_is_fenced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """回合结束后的迟到输出不进入任何一代正文，并留下可见的 warning。"""
    late_gate, late_done = asyncio.Event(), asyncio.Event()
    provider = _LateOutputProvider(
        late_text="回合结束后的迟到输出。", late_gate=late_gate, late_done=late_done
    )
    trace = ModelCallTrace(audit_enabled=False)
    with caplog.at_level("WARNING", logger="muad_agent_runtime.application.executor"):
        events = await _drive_executor(provider, trace=trace)
        late_gate.set()
        await asyncio.wait_for(late_done.wait(), timeout=5)

    deltas = [event for event in events if event.type == DELTA_STREAM_TYPE]
    assert [event.data["delta"] for event in deltas] == ["回合内正文。"]
    assert all("迟到" not in json.dumps(event.data, ensure_ascii=False) for event in events)
    assert trace.reply_generation == 0, "没有失败 ⇒ 不分代"
    assert "reply_delta_fenced" in caplog.text, "围栏必须可见（warning），不是静默吞掉"


async def test_b262_abandoned_attempt_output_is_fenced_across_retry(
    reply_tenant: ReplyTenant,
) -> None:
    """被放弃尝试的迟到输出在新回合进行中到达时同样被 fence；新代以 snapshot 开启。"""
    late_gate, late_done = asyncio.Event(), asyncio.Event()
    provider = _AbandoningProvider(
        late_text="旧尝试迟到输出。", late_gate=late_gate, late_done=late_done
    )
    trace = ModelCallTrace()
    audited = AuditedModelProvider(
        provider,
        RuntimeAuditWriter(
            tenant_id=reply_tenant.tenant_id,
            run_id=uuid.uuid4(),
            task_id=None,
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            session_factory=get_session_factory,
        ),
        model="probe",
        model_trace=trace,
    )
    events: list[ExecutorEvent] = []

    async def drain() -> None:
        events.extend(await _drive_executor(audited, trace=trace))

    task = asyncio.create_task(drain())
    await _wait_until(lambda: provider.attempts == 2, what="重试回合已开始")
    late_gate.set()  # 迟到输出在新回合进行中到达（回合开着，只有尝试围栏能挡它）
    await asyncio.wait_for(late_done.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=WAIT_TIMEOUT_SEC)

    deltas = [event for event in events if event.type == DELTA_STREAM_TYPE]
    assert [event.data["delta"] for event in deltas] == ["第一尝试正文。", "第二尝试正文。"]
    assert [event.data["reply_generation"] for event in deltas] == [0, 1]
    snapshots = [event for event in events if event.type == SNAPSHOT_STREAM_TYPE]
    assert [event.data for event in snapshots] == [{"reply_generation": 1, "text": ""}]
    assert all("迟到" not in json.dumps(event.data, ensure_ascii=False) for event in events)
    assert trace.reply_generation == 1


# --------------------------------------------------------------------------------------
# 场景 5：已提交工具业务键不被重试更换（真实 Skill 工具 + 真实 ToolOperation 行）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _OperationIdentity:
    operation_id: str
    input_hash: str
    business_key: str
    status: str


def _operation_identity(row: dict[str, Any]) -> _OperationIdentity:
    submission = row["submission_json"]
    assert isinstance(submission, dict)
    return _OperationIdentity(
        operation_id=str(row["id"]),
        input_hash=str(row["input_hash"]),
        business_key=str(submission["idempotency_key"]),
        status=str(row["status"]),
    )


async def test_b262_committed_tool_operation_business_key_survives_model_retry(
    reply_tenant: ReplyTenant,
    credentials: ConsoleCredentialsClient,
    supervisor: Any,
) -> None:
    """工具回合提交的真实操作在"部分正文失败→重试"前后身份/业务键逐字节一致。"""
    from httpx import AsyncClient
    from muad_agent_runtime.application.async_tools.control_dispatcher import ControlDispatcher
    from muad_agent_runtime.application.async_tools.runtime import (
        get_control_dispatcher,
        set_control_dispatcher,
    )

    skill = _async_skill()
    probe = ReplyProbe()
    tool_round = ToolCallRound(call_id="call-1", skill_key=SKILL_KEY, input_data={"x": 1})
    probe.expect(
        tool_round,
        CutRound(chunks=(OLD_PARTIAL_TEXT,)),
        tool_round,  # 重试回合重发同一个工具调用（模型重复自己）
        OkRound(chunks=(NEW_GENERATION_TEXT,)),
    )
    probe.hold_after_calls = 1  # 第二个请求（失败回合）先停住，等测试读完已提交事实

    previous = get_control_dispatcher() if _dispatcher_running() else None
    client = AsyncClient()
    try:
        async with _serve(_worker_reject_app()) as worker_url, _serve(_probe_app(probe)) as model_url:
            set_control_dispatcher(
                ControlDispatcher(get_session_factory(), client, worker_url, service_token=None)
            )
            tenant = await _seed_members(reply_tenant, model_url, skill=skill)
            running = asyncio.create_task(
                _start_run(
                    tenant,
                    model_base_url=model_url,
                    credentials=credentials,
                    supervisor=supervisor,
                    agent=AgentSettings(max_model_retries=2),
                )
            )
            await _wait_until(
                lambda: len(probe.calls) == 2 or running.done(),
                what="失败回合到达探针（或整段提前结束）",
            )
            assert not running.done(), "失败回合应停在探针闸门，而不是整段提前结束"
            before = await _wait_operation(tenant.tenant_id, running)
            probe.hold_gate.set()
            run_id, _events = await asyncio.wait_for(running, timeout=WAIT_TIMEOUT_SEC)
    finally:
        set_control_dispatcher(previous)
        await client.aclose()

    after = await _operation_rows(tenant.tenant_id, run_id)
    assert len(after) == 1, "重试不得为已提交工具操作生成第二份操作/业务键"
    identity_after = _operation_identity(after[0])
    assert identity_after == before, "已提交工具操作的业务键（operation id / idempotency key）不被重试更换"
    assert identity_after.business_key == f"runtime-op:{identity_after.operation_id}:submit"
    assert after[0]["source_tool_call_id"] == "call-1"

    run = await _run_row(run_id)
    assert run is not None and run.status == RunStatus.COMPLETED
    rows = await _canonical_rows(run_id)
    assert _delta_text(rows, 0) == OLD_PARTIAL_TEXT
    assert _delta_text(rows, 1) == NEW_GENERATION_TEXT
    assert _visible_body(rows) == NEW_GENERATION_TEXT
    assert len(_rows_of(rows, SNAPSHOT_STREAM_TYPE)) == 1


def _worker_reject_app() -> FastAPI:
    """Worker 桩：对任务提交给出**确定性** 4xx（真实 HTTP），操作按失败收口、Run 不悬停等待。"""
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/internal/tasks")
    async def reject() -> JSONResponse:
        return JSONResponse({"error": {"code": "TASK_REJECTED"}}, status_code=400)

    return app


def _dispatcher_running() -> bool:
    from muad_agent_runtime.application.async_tools.runtime import get_control_dispatcher

    try:
        get_control_dispatcher()
    except RuntimeError:
        return False
    return True


async def _wait_operation(tenant_id: str, running: asyncio.Task[Any]) -> _OperationIdentity:
    """等工具回合的真实操作提交；任务提前失败时把原因带进失败信息（不静默超时）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if running.done():
            running.result()  # 提前结束：把真实异常上抛
        rows = await _operation_rows(tenant_id, await _current_run_id(tenant_id))
        if rows:
            return _operation_identity(rows[0])
        await asyncio.sleep(0.02)
    pytest.fail("等待超时：工具回合的真实操作未提交")


async def _current_run_id(tenant_id: str) -> uuid.UUID:
    async with get_session_factory()() as session:
        run_id = await session.scalar(
            sa.select(RunRecord.id)
            .where(RunRecord.tenant_id == tenant_id, RunRecord.is_deleted.is_(False))
            .order_by(RunRecord.create_time.desc())
            .limit(1)
        )
    assert run_id is not None, "Run 必须已受理"
    return run_id
