"""B-256 / TASK-056：精确 Run 取消、待确认恢复和记忆回执（design §3.4/§3.5/§3.6）。

真实边界（不 mock 存储或租约）：
- 真实 `RunService`/`RunSubmissionService`/`MemoryConfirmationService`/`MemoryService` 业务路径
  （取消、resume、确认消费、记忆写入全部走真实代码，不替换存储或租约）；
- **隔离 PostgreSQL**（`async_tool_database`：每用例一个空库 + 迁移到 head）上的真实
  `runtime.*` 行、真实 `EventWriter` seq、真实行锁与真实准入计数；
- 真实 Runtime HTTP 应用（`/v1/runs/cancel-active`、`/v1/runs/{run}/cancel`、
  `/v1/runs/{run}/resume`、内部 `/internal/webchat/conversations/{id}`）经真实 ASGI 封套与
  内部服务身份门控；确认候选由真实 `MemoryConfirmationService.propose` + `suspend` 落库。
- 执行器用进程内 fake（模型调用不在本契约的断言内）；存储、租约、行锁、事件与记忆写入真实。

关键断言（B-256）：
1. 精确取消：`cancel-active` 带来源（显式会话 / 入口+外线线程）只命中**该来源**的活跃 Run，
   同 Agent/用户的其他会话 Run 完全不动；无来源时保留 (agent,user) 口径；
2. 取消按原 Run 绑定且可重放：同一 Run 重复取消只有一个终态、一次 CANCEL/RUN_COMPLETED 事件；
   未开始执行的 WAITING_INPUT 直接 CAS CANCELLED 并关闭待确认中断；
3. 待确认恢复：API-15 `active_run.confirmation` 给出**完整候选**（prompt/选项/有效期）与原提议
   身份（submission_id/source_event_id）；
4. resume 绑定原 Run/submission/source：绑定不一致显式 `MEMORY_CONFIRMATION_INVALID` 且不消费；
   绑定一致时 source 只消费一次，记忆写入遵守 scope 锁/receipt/history/revision；
5. 锁内复核 WAITING_INPUT/deadline：非等待态显式拒绝、绝对期限过期不再消费；
6. 记忆回执幂等：同幂等键重放返回**原回执**（不写第二次），已消费的 source 再次确认显式拒绝。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_runtime.api.deps import get_platform_settings_client, get_run_service
from muad_agent_runtime.api.runs import router as runs_router
from muad_agent_runtime.api.webchat_history import router as webchat_history_router
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.channel_conversations import ConversationScope, register_pointer
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.memory_confirmation_resume import ConfirmationBinding
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_service import RunService
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelConversation,
    Conversation,
    MemoryCommandReceipt,
    MemoryScope,
    RunInterrupt,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_contracts import RunStatus
from muad_contracts.memory import memory_hash
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.webchat import WebchatConfirmation, WebchatConversationDetail
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from uvicorn import Config as UvicornConfig
from uvicorn import Server as UvicornServer

from tests.agent_runtime.memory_confirmation_resume_support import frozen_snapshot
from tests.agent_runtime.memory_confirmation_support import (
    candidate,
    complete_round,
    source,
)
from tests.agent_runtime.memory_confirmation_support import (
    service as confirmation_service,
)

pytestmark = pytest.mark.integration

#: 内部服务身份（与 `require_internal_service` 同源：env `INTERNAL_SERVICE_TOKEN`）。
INTERNAL_TOKEN = "test-internal-token"
#: 本套件第二个入口（网页/企微两条来源各归自己的入口账号）。
ENTRY_A = uuid.uuid4()
ENTRY_B = uuid.uuid4()
THREAD_A = "thread-a"
THREAD_B = "thread-b"
#: 模型探针地址占位：执行器是进程内 fake，本契约不调用模型。
MODEL_URL = "http://model.invalid/v1"
CONFIRM_OUTCOME = "MEMORY_CONFIRMATION_RESOLVED"


@pytest.fixture(autouse=True)
def _internal_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)


# --------------------------------------------------------------------------------------
# 隔离库 + 真实 Runtime 应用
# --------------------------------------------------------------------------------------


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@contextlib.contextmanager
def _isolated_databases(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> Any:
    """把 Runtime 的进程级 session 工厂指到隔离库（真实 `get_session` 也走同一份）。"""
    from muad_agent_runtime.application import executor, run_service
    from muad_agent_runtime.infrastructure import db

    provider = lambda: factory  # noqa: E731 —— 调用约定是「返回 sessionmaker 的工厂」
    monkeypatch.setattr(db, "get_session_factory", provider)
    monkeypatch.setattr(run_service, "get_session_factory", provider)
    monkeypatch.setattr(executor, "get_session_factory", provider)
    yield


class _FakeExecutor:
    """进程内执行器：只产出确定性增量；模型调用不在本契约的断言范围内。"""

    def __init__(self, request: ExecutorRequest) -> None:
        self._request = request

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(type="message.delta", data={"delta": "已恢复"})


async def _fake_executor_factory(request: ExecutorRequest) -> RunExecutor:
    return _FakeExecutor(request)


class _SuiteSettings:
    """设置源：无平台设置（revision 0）；本套件不经过新 Run 受理路径。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> Any:
        from muad_agent_runtime.application.ports import PlatformSettingsSnapshot

        return PlatformSettingsSnapshot(revision=0, settings={})


@contextlib.asynccontextmanager
async def _runtime_app(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """真实 Runtime HTTP 应用：真实路由 + 真实 RunService（绑定隔离库 session）。"""
    with _isolated_databases(factory, monkeypatch):
        supervisor = ExecutionSupervisor()
        app = FastAPI()
        install_api_foundation(app)
        app.include_router(runs_router)
        app.include_router(webchat_history_router)
        app.state.instance_id = "b256-runtime"
        app.state.execution_supervisor = supervisor
        app.dependency_overrides[get_platform_settings_client] = lambda: _SuiteSettings()

        async def bound_service() -> AsyncIterator[RunService]:
            async with factory() as session:
                yield RunService(
                    session,
                    _unused_resolver(),
                    "b256-runtime",
                    NullPlatformSettingsClient(),
                    executor_factory=_fake_executor_factory,
                    supervisor=supervisor,
                )

        app.dependency_overrides[get_run_service] = bound_service
        try:
            async with _serve(app) as url:
                yield url
        finally:
            await supervisor.close()


def _unused_resolver() -> Any:
    class _Resolver:
        async def resolve(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("control-plane requests must not resolve a new definition")

        async def resolve_model(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("control-plane requests must not resolve a new model")

    return _Resolver()


@contextlib.asynccontextmanager
async def _serve(app: FastAPI) -> AsyncIterator[str]:
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = UvicornServer(UvicornConfig(app, log_level="error", lifespan="off"))
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
        await asyncio.wait_for(task, timeout=5)
        listener.close()


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


# --------------------------------------------------------------------------------------
# 真实行种子（隔离库；只增删本库的行）
# --------------------------------------------------------------------------------------


async def _seed_conversation(
    factory: async_sessionmaker[AsyncSession],
    run: RunRecord,
    *,
    entry_id: uuid.UUID | None,
    thread: str = "",
    title: str = "会话",
) -> uuid.UUID:
    async with factory.begin() as session:
        conversation = Conversation(
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            agent_id=run.agent_id,
            title=title,
            status="ACTIVE",
            last_seq=0,
            channel_account_id=entry_id,
            external_thread_id=thread or None,
            last_message_at=datetime.now(UTC),
        )
        session.add(conversation)
        await session.flush()
        return conversation.id


async def _attach_entry(
    factory: async_sessionmaker[AsyncSession],
    run: RunRecord,
    *,
    entry_id: uuid.UUID,
    thread: str,
    conversation_id: uuid.UUID | None = None,
) -> None:
    """把会话归因到入口并登记入口指针（真实 `register_pointer`）。"""
    target = conversation_id or run.conversation_id
    scope = ConversationScope(
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        agent_id=run.agent_id,
        entry_id=entry_id,
        external_thread_id=thread,
    )
    async with factory.begin() as session:
        conversation = await session.get(Conversation, target)
        assert conversation is not None
        conversation.channel_account_id = entry_id
        conversation.external_thread_id = thread or None
        if target == run.conversation_id:
            # 本 Run 自己的会话指针（与 `RunService._create_run` 同形；详情投影要求二者一致）。
            conversation.last_run_id = run.id
        await register_pointer(session, scope, target)


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    run: RunRecord,
    *,
    conversation_id: uuid.UUID,
    status: RunStatus = RunStatus.CREATED,
    deadline_seconds: int = 600,
    snapshot_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """真实 Run 行 + 会话 `last_run_id` 指针（与 `RunService._create_run` 同形）。"""
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory.begin() as session:
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=run.tenant_id,
                conversation_id=conversation_id,
                user_id=run.user_id,
                agent_id=run.agent_id,
                snapshot_id=snapshot_id,
                status=status,
                input_text="seed",
                trace_id="b256-seed",
                start_time=now,
                deadline_at=now + timedelta(seconds=deadline_seconds),
            )
        )
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.last_run_id = run_id
    return run_id


async def _run_row(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> RunRecord:
    async with factory() as session:
        row = await session.get(RunRecord, run_id)
        assert row is not None
        return row


async def _interrupt_rows(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> list[RunInterrupt]:
    async with factory() as session:
        return list(
            await session.scalars(
                sa.select(RunInterrupt)
                .where(RunInterrupt.run_id == run_id)
                .order_by(RunInterrupt.create_time)
            )
        )


async def _force_run_state(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    status: RunStatus,
    deadline_at: datetime | None = None,
) -> None:
    """把 Run 行置于指定状态（真实行更新）：证明复核发生在锁内，而不是靠时序巧合。"""
    values: dict[str, Any] = {"status": status}
    if deadline_at is not None:
        values["deadline_at"] = deadline_at
    async with factory.begin() as session:
        await session.execute(sa.update(RunRecord).where(RunRecord.id == run_id).values(**values))


async def _pending_binding(
    factory: async_sessionmaker[AsyncSession], run: RunRecord
) -> ConfirmationBinding:
    """待确认的原提议身份（submission + source event）：从真实提议行读回，不猜。"""
    interrupt = (await _interrupt_rows(factory, run.id))[0]
    assert interrupt.proposal_json is not None
    return ConfirmationBinding(
        submission_id=uuid.UUID(str(interrupt.proposal_json["submission_id"])),
        source_event_id=uuid.UUID(str(interrupt.proposal_json["evidence"]["source_event_id"])),
    )


async def _assert_single_confirmation_write(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, proposal_id: uuid.UUID
) -> None:
    """一次确认恰好写一份：回执、记忆行、完整 after 历史、幂等 receipt 与 scope revision。"""
    resolved = await _events(factory, run_id, CONFIRM_OUTCOME)
    assert len(resolved) == 1
    receipt = resolved[0].payload_json
    assert receipt["outcome"] == "CONFIRMED" and receipt["saved"] is True
    assert receipt["memory_key"] == "color" and receipt["version"] == 1
    async with factory() as session:
        stored = (await session.scalars(sa.select(UserMemory))).one()
        scope = (await session.scalars(sa.select(MemoryScope))).one()
        history = list(await session.scalars(sa.select(UserMemoryHistory)))
        receipts = list(await session.scalars(sa.select(MemoryCommandReceipt)))
    assert stored.content_json == {"value": "blue"} and stored.author == "USER"
    assert stored.version == stored.confirmed_count == 1
    assert scope.revision == 1  # 一次变更恰好推进一次 scope revision
    assert len(history) == len(receipts) == 1
    assert history[0].after_json["content_json"] == {"value": "blue"}
    assert history[0].after_json["version"] == 1
    assert receipts[0].command_id.startswith(f"confirmation:{proposal_id}:")
    assert (await _interrupt_rows(factory, run_id))[0].status == "RESOLVED"


async def _count(factory: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with factory() as session:
        return int(await session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, event_type: str
) -> list[CanonicalEvent]:
    async with factory() as session:
        return list(
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(CanonicalEvent.run_id == run_id, CanonicalEvent.event_type == event_type)
                .order_by(CanonicalEvent.seq)
            )
        )


async def _post(
    url: str, path: str, tenant_id: str, payload: dict[str, Any], **extra: Any
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{url}{path}", headers={**_headers(tenant_id), **extra}, json=payload
        )


def _sse_events(body: str) -> list[dict[str, Any]]:
    import json

    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data = [
            line[len("data:") :].lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


# --------------------------------------------------------------------------------------
# B-256①：精确取消（来源身份，不误伤同 Agent 其他会话的 Run）
# --------------------------------------------------------------------------------------


async def _cancel_two_sources(
    url: str,
    run: RunRecord,
    web_conversation: uuid.UUID,
    wecom_conversation: uuid.UUID,
) -> tuple[httpx.Response, httpx.Response]:
    """两条来源的停止命令**同时**发出：网页给显式会话，企微给入口 + 外线线程（入口指针）。"""
    identity = {"agent_id": str(run.agent_id), "platform_user_id": str(run.user_id)}
    results = await asyncio.gather(
        _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {**identity, "source": {"conversation_id": str(web_conversation), "entry_id": str(ENTRY_A)}},
        ),
        _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {**identity, "source": {"entry_id": str(ENTRY_B), "external_thread_id": THREAD_B}},
        ),
    )
    return results[0], results[1]


async def test_b256_source_scoped_cancel_hits_only_its_own_conversation(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网页会话与企微入口同时有活跃 Run：来源级取消只命中自己的那一个（RISK-F03）。

    真实并发：两个来源的停止命令同时发出，各自解析到自己的会话与 Run；断言两个 Run 各自
    恰好被取消一次、对方完全不动（`admission_released_at` 与终态都只落在一个 Run 上）。
    """
    factory = _factory(async_tool_database)
    _, run, _, _ = await source(async_tool_database)
    web_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_A)
    wecom_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_B, thread=THREAD_B)
    web_run = await _seed_run(factory, run, conversation_id=web_conversation)
    wecom_run = await _seed_run(
        factory, run, conversation_id=wecom_conversation, status=RunStatus.WAITING_INPUT
    )

    async with _runtime_app(factory, monkeypatch) as url:
        web, wecom = await _cancel_two_sources(url, run, web_conversation, wecom_conversation)

    assert web.status_code == 200, web.text
    assert wecom.status_code == 200, wecom.text
    assert web.json()["data"]["run_id"] == str(web_run)
    assert wecom.json()["data"]["run_id"] == str(wecom_run)
    assert web.json()["data"]["status"] == str(RunStatus.CANCELLED)
    assert wecom.json()["data"]["status"] == str(RunStatus.CANCELLED)

    # 每个 Run 只被自己的来源取消一次：终态、释放时间戳、取消事件各恰好一份。
    for run_id in (web_run, wecom_run):
        row = await _run_row(factory, run_id)
        assert row.status == RunStatus.CANCELLED and row.cancel_requested
        assert row.admission_released_at is not None
        assert len(await _events(factory, run_id, "CANCEL")) == 1
        assert len(await _events(factory, run_id, "RUN_COMPLETED")) == 1


async def test_b256_source_without_active_run_does_not_touch_other_conversations(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """来源自己空闲时 `NO_ACTIVE_RUN`：绝不回退去取消同用户/同 Agent 其他会话的 Run。"""
    factory = _factory(async_tool_database)
    _, run, _, _ = await source(async_tool_database)
    idle_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_A)
    busy_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_B, thread=THREAD_B)
    busy_run = await _seed_run(factory, run, conversation_id=busy_conversation, status=RunStatus.RUNNING)

    async with _runtime_app(factory, monkeypatch) as url:
        idle = await _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {
                "agent_id": str(run.agent_id),
                "platform_user_id": str(run.user_id),
                "source": {"conversation_id": str(idle_conversation), "entry_id": str(ENTRY_A)},
            },
        )
        # 无来源的既有口径仍然成立（调用方没有来源事实时按 (agent,user) 最近活跃 Run）。
        legacy = await _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {"agent_id": str(run.agent_id), "platform_user_id": str(run.user_id)},
        )

    assert idle.status_code == 404 and idle.json()["code"] == "NO_ACTIVE_RUN"
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["data"]["run_id"] == str(busy_run)
    row = await _run_row(factory, busy_run)
    assert row.status == RunStatus.RUNNING and row.cancel_requested  # 协作取消已受理
    assert row.admission_released_at is None


async def test_b256_run_bound_cancel_is_replayable_with_a_single_terminal_state(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网页停止按**原 Run** 定位（API-08）：重复取消只有一个终态、一份取消事件。"""
    factory = _factory(async_tool_database)
    _, run, _, _ = await source(async_tool_database)
    conversation = await _seed_conversation(factory, run, entry_id=ENTRY_A)
    target = await _seed_run(
        factory, run, conversation_id=conversation, status=RunStatus.WAITING_INPUT
    )

    async with _runtime_app(factory, monkeypatch) as url:
        first = await _post(url, f"/v1/runs/{target}/cancel", run.tenant_id, {})
        second = await _post(url, f"/v1/runs/{target}/cancel", run.tenant_id, {})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["status"] == str(RunStatus.CANCELLED)
    assert second.json()["data"]["status"] == str(RunStatus.CANCELLED)
    assert second.json()["data"]["cancel_requested"] is True
    row = await _run_row(factory, target)
    assert row.status == RunStatus.CANCELLED
    assert len(await _events(factory, target, "CANCEL")) == 1
    assert len(await _events(factory, target, "RUN_COMPLETED")) == 1


# --------------------------------------------------------------------------------------
# B-256②/③：待确认恢复（完整候选 + 原提议身份）
# --------------------------------------------------------------------------------------


async def _pending_confirmation(
    engine: AsyncEngine,
) -> tuple[async_sessionmaker[AsyncSession], RunRecord, uuid.UUID, uuid.UUID]:
    """真实 `propose` + 整轮完成 + `suspend`：待确认中断、提议与展示证据全部由生产代码落库。"""
    factory, run, identity, submission_id = await source(engine)
    await frozen_snapshot(factory, run, MODEL_URL, MemoryPolicySettings())
    memory = confirmation_service(factory, identity, submission_id)
    await memory.propose(candidate(source_quote="记住蓝色"), call_id="remember-1")
    proposal_id = memory.suspend_request.interrupt_id
    await complete_round(factory, run)
    await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))
    return factory, run, submission_id, proposal_id


async def _confirmation_detail(url: str, run: RunRecord) -> WebchatConfirmation:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{url}/internal/webchat/conversations/{run.conversation_id}",
            headers=_headers(run.tenant_id),
            params={
                "user_id": str(run.user_id),
                "agent_id": str(run.agent_id),
                "entry_id": str(ENTRY_A),
            },
        )
    assert response.status_code == 200, response.text
    detail = WebchatConversationDetail.model_validate(response.json()["data"])
    assert detail.active_run is not None and detail.active_run.status == "WAITING_INPUT"
    confirmation = detail.active_run.confirmation
    assert confirmation is not None
    return confirmation


async def test_b256_pending_confirmation_projects_the_complete_candidate(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-15 待确认恢复：完整候选（prompt/选项/有效期）+ 原提议身份（submission/source）。"""
    factory, run, submission_id, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)

    async with _runtime_app(factory, monkeypatch) as url:
        confirmation = await _confirmation_detail(url, run)
        interrupt = (await _interrupt_rows(factory, run.id))[0]
        proposal_json = interrupt.proposal_json
        assert proposal_json is not None
        source_event_id = uuid.UUID(proposal_json["evidence"]["source_event_id"])
        # 投影的候选就是**能被确认的那一份**：绑定字段与展示 prompt 逐项对齐。
        assert confirmation.submission_id == submission_id
        assert confirmation.source_event_id == source_event_id
        assert confirmation.prompt == interrupt.prompt_text
        assert "color" in confirmation.prompt and "blue" in confirmation.prompt
        assert confirmation.expires_at == interrupt.expires_at
        assert [option.label for option in confirmation.options] == ["确认", "拒绝"]
        assert [option.value for option in confirmation.options] == [
            f"确认 {proposal_id}",
            f"拒绝 {proposal_id}",
        ]

        # 选项值就是 resume 能消费的文本（同一份 resume 语义，不是另拼一份）。
        accepted = await _post(
            url,
            f"/v1/runs/{run.id}/resume",
            run.tenant_id,
            {
                "platform_user_id": str(run.user_id),
                "input": {"id": uuid.uuid4().hex, "text": confirmation.options[0].value},
                "submission_id": str(confirmation.submission_id),
                "source_event_id": str(confirmation.source_event_id),
            },
        )
        assert accepted.status_code == 200, accepted.text

    saved = await _count(factory, UserMemory)
    assert saved == 1
    assert (await _interrupt_rows(factory, run.id))[0].status == "RESOLVED"


# --------------------------------------------------------------------------------------
# B-256④/⑤：resume 绑定原 Run/submission/source + 锁内复核
# --------------------------------------------------------------------------------------


async def _resume(
    url: str,
    run: RunRecord,
    *,
    text: str,
    submission_id: uuid.UUID | None,
    source_event_id: uuid.UUID | None,
    key: str | None = None,
    input_id: str | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "platform_user_id": str(run.user_id),
        "input": {"id": input_id or uuid.uuid4().hex, "text": text},
    }
    if submission_id is not None:
        payload["submission_id"] = str(submission_id)
    if source_event_id is not None:
        payload["source_event_id"] = str(source_event_id)
    return await _post(
        url,
        f"/v1/runs/{run.id}/resume",
        run.tenant_id,
        payload,
        **({"Idempotency-Key": key} if key else {}),
    )


async def test_b256_resume_rejects_other_submission_or_source_and_keeps_waiting(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绑定不一致（别的 submission/source）显式 `MEMORY_CONFIRMATION_INVALID`，不消费任何东西。"""
    factory, run, submission_id, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)

    async with _runtime_app(factory, monkeypatch) as url:
        wrong_submission = await _resume(
            url,
            run,
            text=f"确认 {proposal_id}",
            submission_id=uuid.uuid4(),
            source_event_id=uuid.uuid4(),
        )
        wrong_source = await _resume(
            url,
            run,
            text=f"确认 {proposal_id}",
            submission_id=submission_id,
            source_event_id=uuid.uuid4(),
        )
        # 半绑定不是请求语义：校验层直接拒绝（不给应用层留「只有一半」的中间态）。
        half_binding = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=submission_id, source_event_id=None
        )

    assert wrong_submission.status_code == 400
    assert wrong_submission.json()["code"] == "MEMORY_CONFIRMATION_INVALID"
    assert wrong_source.status_code == 400
    assert wrong_source.json()["code"] == "MEMORY_CONFIRMATION_INVALID"
    assert half_binding.status_code == 422
    assert half_binding.json()["code"] == "COMMON_VALIDATION_ERROR"
    # 三次拒绝都没有消费 source、没有写记忆、等待态原样保留。
    assert (await _interrupt_rows(factory, run.id))[0].status == "WAITING"
    assert (await _run_row(factory, run.id)).status == RunStatus.WAITING_INPUT
    assert await _count(factory, UserMemory) == 0
    assert await _events(factory, run.id, CONFIRM_OUTCOME) == []


async def test_b256_resume_consumes_source_once_and_replays_the_same_receipt(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认一致时 source 只消费一次：记忆写入 + 幂等 receipt + 完整 after 历史 + scope revision。"""
    factory, run, submission_id, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    binding = await _pending_binding(factory, run)
    assert binding.submission_id == submission_id  # 提议绑定的是真实来源提交
    async with factory() as session:
        before = await session.scalar(
            sa.select(MemoryScope.revision).where(MemoryScope.tenant_id == run.tenant_id)
        )
    assert before is None  # 候选阶段只锁行、不建 scope 变更

    key = f"resume:{uuid.uuid4().hex}"
    # 重试沿用**原消息身份**（RULE-02）：同键 + 同 message_id 才是同一次提交的重放。
    input_id = uuid.uuid4().hex

    async with _runtime_app(factory, monkeypatch) as url:
        accepted = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=key, input_id=input_id,
        )
        assert accepted.status_code == 200, accepted.text
        first_events = _sse_events(accepted.text)
        replay = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=key, input_id=input_id,
        )
        assert replay.status_code == 200, replay.text
        replayed_events = _sse_events(replay.text)

    assert first_events == replayed_events  # 幂等重放：原回执，不重新消费
    await _assert_single_confirmation_write(factory, run.id, proposal_id)


async def test_b256_source_cannot_be_consumed_twice_after_resolution(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已消费的 source 不会第二次消费：再次确认只回放终态，不复活、不双写。"""
    factory, run, _, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    binding = await _pending_binding(factory, run)

    async with _runtime_app(factory, monkeypatch) as url:
        accepted = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=f"resume:{uuid.uuid4().hex}",
        )
        assert accepted.status_code == 200, accepted.text
        # 另一条消息（新幂等键）再次确认同一个 source：Run 已是终态，只回放终态（旧 Run 不重开）。
        again = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=f"resume:{uuid.uuid4().hex}",
        )

    assert again.status_code == 200, again.text
    terminal = _sse_events(again.text)[-1]
    assert terminal["type"] == "run.completed"
    assert terminal["data"]["status"] == str(RunStatus.COMPLETED)
    await _assert_single_confirmation_write(factory, run.id, proposal_id)  # 仍然只有一次写入


async def test_b256_resume_rechecks_waiting_input_and_absolute_deadline_in_lock(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁内复核：非等待态显式拒绝；绝对期限过期后不再消费（提议与记忆都保持原样）。"""
    factory, run, _, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    binding = await _pending_binding(factory, run)

    async with _runtime_app(factory, monkeypatch) as url:
        # 非等待态（RUNNING）：REVISION_CONFLICT，绝不把普通消息当确认消费。
        await _force_run_state(factory, run.id, status=RunStatus.RUNNING)
        busy = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=f"resume:{uuid.uuid4().hex}",
        )
        assert busy.status_code == 409
        assert busy.json()["code"] == "REVISION_CONFLICT"

        # 绝对期限已过（仍停在 WAITING_INPUT）：MEMORY_CONFIRMATION_EXPIRED，不消费 source。
        await _force_run_state(
            factory, run.id, status=RunStatus.WAITING_INPUT,
            deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        expired = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=f"resume:{uuid.uuid4().hex}",
        )

    assert expired.status_code == 410
    assert expired.json()["code"] == "MEMORY_CONFIRMATION_EXPIRED"
    assert (await _interrupt_rows(factory, run.id))[0].status == "WAITING"
    assert await _count(factory, UserMemory) == 0
    assert await _events(factory, run.id, CONFIRM_OUTCOME) == []


async def test_b256_cancelled_waiting_run_closes_the_confirmation(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消未开始执行的待确认 Run：中断关闭、取消事件落库；确认候选随之消失（不再可恢复）。"""
    factory, run, _, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)

    async with _runtime_app(factory, monkeypatch) as url:
        cancelled = await _post(url, f"/v1/runs/{run.id}/cancel", run.tenant_id, {})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["status"] == str(RunStatus.CANCELLED)
        async with httpx.AsyncClient(timeout=30.0) as client:
            detail = await client.get(
                f"{url}/internal/webchat/conversations/{run.conversation_id}",
                headers=_headers(run.tenant_id),
                params={
                    "user_id": str(run.user_id),
                    "agent_id": str(run.agent_id),
                    "entry_id": str(ENTRY_A),
                },
            )
        assert detail.status_code == 200, detail.text
        assert WebchatConversationDetail.model_validate(detail.json()["data"]).active_run is None

    interrupts = await _interrupt_rows(factory, run.id)
    assert [row.status for row in interrupts] == ["CANCELLED"]
    assert interrupts[0].resolution_json == {"reason": "cancelled"}
    assert len(await _events(factory, run.id, "CANCEL")) == 1
    assert await _count(factory, UserMemory) == 0


async def test_b256_replayed_binding_change_is_rejected_as_idempotency_mismatch(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绑定是请求语义的一部分：同幂等键换绑定 → `IDEMPOTENCY_MISMATCH`，不重放别的回执。"""
    factory, run, _, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    binding = await _pending_binding(factory, run)
    key = f"resume:{uuid.uuid4().hex}"

    async with _runtime_app(factory, monkeypatch) as url:
        accepted = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=key,
        )
        assert accepted.status_code == 200, accepted.text
        changed = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=uuid.uuid4(),
            source_event_id=binding.source_event_id, key=key,
        )

    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert len(await _events(factory, run.id, CONFIRM_OUTCOME)) == 1


async def test_b256_unattributed_entry_cannot_reach_another_entry_conversation(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式会话核属（tenant/user/agent/入口 同时成立）：借其他入口的会话取消一律拒绝。"""
    factory = _factory(async_tool_database)
    _, run, _, _ = await source(async_tool_database)
    foreign = await _seed_conversation(factory, run, entry_id=ENTRY_B, thread=THREAD_B)
    foreign_run = await _seed_run(factory, run, conversation_id=foreign, status=RunStatus.RUNNING)

    async with _runtime_app(factory, monkeypatch) as url:
        borrowed = await _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {
                "agent_id": str(run.agent_id),
                "platform_user_id": str(run.user_id),
                "source": {"conversation_id": str(foreign), "entry_id": str(ENTRY_A)},
            },
        )

    assert borrowed.status_code == 404 and borrowed.json()["code"] == "COMMON_NOT_FOUND"
    row = await _run_row(factory, foreign_run)
    assert row.status == RunStatus.RUNNING and not row.cancel_requested


async def test_b256_entry_pointer_source_resolves_the_current_conversation(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入口+外线线程（无显式会话）：按当前指针定位该来源的活跃 Run，不创建会话、不动别的来源。"""
    factory = _factory(async_tool_database)
    _, run, _, _ = await source(async_tool_database)
    pointer_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    other_conversation = await _seed_conversation(factory, run, entry_id=ENTRY_B, thread=THREAD_B)
    await _attach_entry(
        factory, run, entry_id=ENTRY_A, thread=THREAD_A, conversation_id=pointer_conversation
    )
    pointer_run = await _seed_run(factory, run, conversation_id=pointer_conversation)
    other_run = await _seed_run(factory, run, conversation_id=other_conversation, status=RunStatus.RUNNING)

    async with _runtime_app(factory, monkeypatch) as url:
        stopped = await _post(
            url,
            "/v1/runs/cancel-active",
            run.tenant_id,
            {
                "agent_id": str(run.agent_id),
                "platform_user_id": str(run.user_id),
                "source": {"entry_id": str(ENTRY_A), "external_thread_id": THREAD_A},
            },
        )

    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["data"]["run_id"] == str(pointer_run)
    async with factory() as session:
        pointers = list(await session.scalars(sa.select(ChannelConversation)))
    assert len(pointers) == 1 and pointers[0].conversation_id == pointer_conversation
    assert (await _run_row(factory, pointer_run)).status == RunStatus.CANCELLED
    assert (await _run_row(factory, other_run)).status == RunStatus.RUNNING


async def test_b256_confirmation_receipt_matches_the_persisted_proposal(
    async_tool_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回执与提议同源：`resolution_json`/canonical 事件/记忆行的 key 与版本逐项一致。"""
    factory, run, _, proposal_id = await _pending_confirmation(async_tool_database)
    await _attach_entry(factory, run, entry_id=ENTRY_A, thread=THREAD_A)
    binding = await _pending_binding(factory, run)
    interrupt = (await _interrupt_rows(factory, run.id))[0]
    assert interrupt.proposal_json is not None
    shown = await _events(factory, run.id, "MEMORY_CONFIRMATION_REQUIRED")
    assert len(shown) == 1 and shown[0].payload_json["proposal_hash"] == memory_hash(
        interrupt.proposal_json
    )

    async with _runtime_app(factory, monkeypatch) as url:
        accepted = await _resume(
            url, run, text=f"确认 {proposal_id}", submission_id=binding.submission_id,
            source_event_id=binding.source_event_id, key=f"resume:{uuid.uuid4().hex}",
        )
        assert accepted.status_code == 200, accepted.text

    resolved = (await _interrupt_rows(factory, run.id))[0]
    assert resolved.status == "RESOLVED"
    assert resolved.resolution_json is not None
    assert resolved.resolution_json["outcome"] == "CONFIRMED"
    assert resolved.resolution_json["memory_key"] == "color"
    assert resolved.resolution_json["version"] == 1
    event = (await _events(factory, run.id, CONFIRM_OUTCOME))[0]
    assert event.source_event_id == proposal_id  # 回执挂在被消费的提议上
    async with factory() as session:
        stored = (await session.scalars(sa.select(UserMemory))).one()
    assert stored.memory_key == "color" and stored.version == 1
