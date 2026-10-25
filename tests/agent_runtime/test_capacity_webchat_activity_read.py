"""B-264 / B-03 / TASK-064：活动收件箱增量读取与独立已读（design §3.4 API-11、§3.6）。

真实边界（不 mock 存储或租约）：

- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + 迁移到 head，跑完 drop）：真实
  `runtime.channel_notification_feed` / `channel_notification` / `tool_operation` /
  `tool_result_inbox` / `artifact` 行与真实完整唯一约束、行锁；
- **真实 Runtime HTTP 应用**：`/internal/webchat/notifications`、`.../{id}/read`、
  `/internal/webchat/task-results` 的真实路由与 `require_internal_service` 服务门控；
- **真实 Runtime 业务服务**：`RunService.start/_finalize_run`（真实终态事务）写通知，
  `write_run_notification` 是唯一写入接缝；Task 来源核验走真实 ToolResultInbox/ToolOperation 行；
- **真实故障注入**：`runtime.channel_notification` 上的 BEFORE INSERT 触发器（真实 DDL，只落在
  隔离库）让 Task 结果通知写入失败，验证"持久入箱后才回 persisted"。

关键断言：

B-264

1. 按用户 feed seq 增量分页：`after_seq`/`next_seq`/`through_seq` 水位推进，跨页读取无跳过、
   无重复；offset 页与水位语义一致；新提交只出现在水位之后；
2. 显式已读与投递状态是两条独立事实：标记已读不碰 `delivery_state`/投递回执，送达不代替已读，
   已读不要求送达；重复标记幂等（返回原 `read_at`）；只标记明确读过的行；
3. 受信 Task 结果入箱端口：核真实 Task/Run/用户/入口归属与 artifact 的 Task 来源；持久入箱
   （提交）后才回 `persisted=true`；同 `delivery_key` 重投命中原回执（不新占 seq、不新增未读）；
   写入失败整体回滚；安全投影不含投递/租约/存储内部字段。

B-03

4. 通知 seq 按用户提交顺序推进：逐条提交递增、无缺口，不同用户各自独立，并发提交仍无重复序号；
5. 事件追尾与历史分页无跳过：冻结水位的消息分页无跳过/重复，Run 事件追尾按 seq 严格递增；
   读游标不能跨会话/跨用户（显式 `CURSOR_AHEAD`，不静默夹取）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.channel_notification_queries import (
    ChannelNotificationQueryService,
)
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    DELIVERY_PENDING,
    KIND_RUN_COMPLETED,
    KIND_TASK_RESULT,
    RunNotificationReceipt,
    task_result_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)
from muad_common import SharedSettings
from muad_contracts import (
    ChannelContext,
    CompletionMode,
    MessageInput,
    OperationStatus,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
    RunStatus,
)
from muad_contracts.webchat_events import WebchatTaskResultRegistration
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

NOTIFICATIONS_PATH = "/internal/webchat/notifications"
TASK_RESULTS_PATH = "/internal/webchat/task-results"
MESSAGES_PATH = "/internal/webchat/conversations/{conversation_id}/messages"
DRAIN_TIMEOUT_SEC = 30.0
MODEL_URL = "http://model.invalid/v1"
#: 通知 DTO 的完整公开字段（安全投影：多一个字段都算泄露内部事实）。
NOTIFICATION_FIELDS = {
    "id",
    "seq",
    "entry_id",
    "agent_id",
    "conversation_id",
    "run_id",
    "task_id",
    "event_id",
    "kind",
    "summary",
    "attachments",
    "created_at",
    "read_at",
}
#: 永不进入公开投影的投递/租约/存储内部字段（B-264 安全断言）。
FORBIDDEN_FIELDS = {
    "delivery_state",
    "delivery_key",
    "delivery_result",
    "lease_owner",
    "lease_epoch",
    "lease_until",
    "next_attempt_at",
    "attempt",
    "storage_key",
    "payload_json",
    "preview_text",
    "is_deleted",
}


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL + 真实 HTTP 应用
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityTenant:
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    entry_id: uuid.UUID


@pytest.fixture
async def activity_tenant(activity_db: dict[str, str]) -> ActivityTenant:
    """租户身份 + 隔离库环境：**每个用例都经 `activity_db`**，任何用例都不可能落到共享 dev 库。"""
    return ActivityTenant(
        tenant_id=f"activity-read-{uuid.uuid4().hex[:12]}",
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
    )


@pytest.fixture
async def activity_db(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[dict[str, str]]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield async_tool_datastore
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
async def client(activity_db: dict[str, str]) -> AsyncIterator[AsyncClient]:
    """真实 FastAPI 应用（ASGI）+ 真实服务身份门控；会话工厂已指向隔离库。"""
    from muad_agent_runtime.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def _headers(tenant: ActivityTenant, *, token: str | None | object = ...) -> dict[str, str]:
    """受信身份头：`token=None` 即完全省略 `X-Internal-Service`（门控拒绝路径）。"""
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if token is None:
        return headers
    headers["X-Internal-Service"] = (
        token if isinstance(token, str) else (SharedSettings().internal_service_token or "")
    )
    return headers


async def _list_notifications(
    client: AsyncClient,
    tenant: ActivityTenant,
    *,
    after_seq: int = 0,
    page: int = 1,
    page_size: int = 50,
    user_id: uuid.UUID | None = None,
    token: str | None | object = ...,
) -> dict[str, Any]:
    response = await client.get(
        NOTIFICATIONS_PATH,
        headers=_headers(tenant, token=token),
        params={
            "user_id": str(user_id or tenant.platform_user_id),
            "after_seq": after_seq,
            "page": page,
            "page_size": page_size,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _mark_read(
    client: AsyncClient,
    tenant: ActivityTenant,
    notification_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    token: str | None | object = ...,
) -> httpx.Response:
    return await client.post(
        f"{NOTIFICATIONS_PATH}/{notification_id}/read",
        headers=_headers(tenant, token=token),
        params={"user_id": str(user_id or tenant.platform_user_id)},
    )


# --------------------------------------------------------------------------------------
# 真实行种子与读取（只读本租户的行）
# --------------------------------------------------------------------------------------


#: 默认入口归属的哨兵：显式传 `None` 即"无入口归属的会话"。
_DEFAULT_ENTRY = object()


async def _seed_conversation(
    tenant: ActivityTenant,
    *,
    user_id: uuid.UUID | None = None,
    entry_id: uuid.UUID | None | object = _DEFAULT_ENTRY,
    status: str = "ACTIVE",
) -> uuid.UUID:
    entry = tenant.entry_id if entry_id is _DEFAULT_ENTRY else entry_id
    assert entry is None or isinstance(entry, uuid.UUID)
    conversation = Conversation(
        tenant_id=tenant.tenant_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=tenant.agent_id,
        title="活动收件箱",
        status=status,
        last_seq=0,
        channel_account_id=entry,
        external_thread_id="web-1",
        last_message_at=datetime.now(UTC),
    )
    async with runtime_db.get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


async def _seed_run(
    tenant: ActivityTenant,
    conversation_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    status: RunStatus = RunStatus.COMPLETED,
) -> RunRecord:
    now = datetime.now(UTC)
    run = RunRecord(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status=status,
        input_text="seed",
        trace_id=f"activity-read-{uuid.uuid4().hex[:8]}",
        start_time=now,
        end_time=now if status in (RunStatus.COMPLETED, RunStatus.FAILED) else None,
        deadline_at=now + timedelta(seconds=600),
    )
    async with runtime_db.get_session_factory()() as session:
        session.add(run)
        await session.commit()
    return run


async def _write_notification(
    tenant: ActivityTenant,
    run_id: uuid.UUID,
    *,
    kind: str = KIND_RUN_COMPLETED,
    delivery_key: str | None = None,
    delivery_state: str = DELIVERY_NOT_REQUIRED,
) -> RunNotificationReceipt:
    async with runtime_db.get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        receipt = await write_run_notification(
            session,
            run=run,
            kind=kind,
            delivery_key=delivery_key or f"probe:{uuid.uuid4()}",
            delivery_state=delivery_state,
        )
        assert receipt is not None
        await session.commit()
        return receipt


async def _notification_rows(tenant_id: str, user_id: uuid.UUID) -> dict[uuid.UUID, ChannelNotification]:
    async with runtime_db.get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(ChannelNotification).where(
                ChannelNotification.tenant_id == tenant_id,
                ChannelNotification.user_id == user_id,
            )
        )
        return {row.id: row for row in rows}


async def _feed_last_seq(tenant_id: str, user_id: uuid.UUID) -> int | None:
    async with runtime_db.get_session_factory()() as session:
        last = await session.scalar(
            sa.select(ChannelNotificationFeed.last_seq).where(
                ChannelNotificationFeed.tenant_id == tenant_id,
                ChannelNotificationFeed.user_id == user_id,
            )
        )
    return None if last is None else int(last)


async def _conversation_last_seq(conversation_id: uuid.UUID) -> int:
    async with runtime_db.get_session_factory()() as session:
        last = await session.scalar(
            sa.select(Conversation.last_seq).where(Conversation.id == conversation_id)
        )
    assert last is not None
    return int(last)


async def _mark_delivered(notification_id: uuid.UUID) -> None:
    """模拟交付路径（TASK-065/066）的 ack 结果：只改投递事实，不碰已读。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification"
                " SET delivery_state = 'DELIVERED',"
                "     delivery_result = jsonb_build_object('outcome', 'DELIVERED')"
                " WHERE id = :id"
            ),
            {"id": notification_id},
        )
        await session.commit()


async def _hide_notification(notification_id: uuid.UUID) -> None:
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text("UPDATE runtime.channel_notification SET is_deleted = true WHERE id = :id"),
            {"id": notification_id},
        )
        await session.commit()


async def _visible_message_rows(conversation_id: uuid.UUID) -> list[CanonicalEvent]:
    async with runtime_db.get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.event_type.in_(("USER_MESSAGE", "ASSISTANT_MESSAGE")),
                    CanonicalEvent.is_deleted.is_(False),
                )
                .order_by(CanonicalEvent.seq)
            )
        )


async def _run_stream_rows(run_id: uuid.UUID) -> list[CanonicalEvent]:
    async with runtime_db.get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.stream_type.is_not(None),
                    CanonicalEvent.is_deleted.is_(False),
                )
                .order_by(CanonicalEvent.seq)
            )
        )


# --------------------------------------------------------------------------------------
# 真实 RunService 驱动（终态事务写通知；模型调用不在本契约断言内）
# --------------------------------------------------------------------------------------


def _resolved(tenant: ActivityTenant) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=tenant.agent_id, key="activity-agent", revision=1, instructions="", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="probe",
            base_url=MODEL_URL,
            api_key="sk-probe",
            params={},
        ),
        skills=[],
        mcp_servers=[],
    )


class _Resolver:
    """定义解析替身：只回答本套件固定定义；模型调用不在断言内。"""

    def __init__(self, response: ResolveDefinitionResponse) -> None:
        self._response = response

    async def resolve(self, request: Any, *, tenant_id: str, trace_id: str = "") -> Any:
        return self._response

    async def resolve_model(self, request: Any, *, tenant_id: str, trace_id: str = "") -> Any:
        from muad_api import AppError
        from muad_api.error_codes import ErrorCode

        raise AppError(ErrorCode.COMMON_NOT_FOUND)


class _ActivitySettings:
    """设置源：足够大的准入容量（本契约不验证准入阈值边界）。"""

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "capacity": {"max_open_runs_per_tenant": 100_000, "max_open_runs_per_user": 100}
            },
        )


class _Executor:
    """进程内执行器：产出确定性增量（真实模型调用不在本契约断言内）。"""

    def __init__(self, request: ExecutorRequest) -> None:
        self._request = request

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(type="message.delta", data={"delta": "最终正文"})


def _executor_factory() -> ExecutorFactory:
    async def factory(request: ExecutorRequest) -> RunExecutor:
        return _Executor(request)

    return factory


def _request(
    tenant: ActivityTenant, conversation_id: uuid.UUID, *, text: str = "写一条消息"
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(
            type="WEBCHAT", bot_id=str(tenant.entry_id), external_user_id="web-1"
        ),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


@asynccontextmanager
async def _service(tenant: ActivityTenant, supervisor: ExecutionSupervisor) -> AsyncIterator[RunService]:
    session = runtime_db.get_session_factory()()
    try:
        yield RunService(
            session,
            _Resolver(_resolved(tenant)),
            "activity-read",
            _ActivitySettings(),
            executor_factory=_executor_factory(),
            supervisor=supervisor,
            credentials_client=None,
        )
    finally:
        await session.close()


async def _start(
    tenant: ActivityTenant, conversation_id: uuid.UUID, supervisor: ExecutionSupervisor
) -> RunStart:
    async with _service(tenant, supervisor) as service:
        return await service.start(
            _request(tenant, conversation_id),
            tenant.tenant_id,
            idempotency_key=f"key-{uuid.uuid4()}",
        )


async def _drain(start: RunStart) -> list[ExecutorEvent]:
    events: list[ExecutorEvent] = []

    async def collect() -> None:
        async for event in start.events:
            events.append(event)

    await asyncio.wait_for(collect(), timeout=DRAIN_TIMEOUT_SEC)
    return events


# --------------------------------------------------------------------------------------
# 真实 Task 来源（ToolResultInbox/ToolOperation/Artifact）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskSource:
    task_id: uuid.UUID
    run: RunRecord
    artifact_id: uuid.UUID
    receipt_event_id: uuid.UUID


async def _seed_task_source(
    tenant: ActivityTenant,
    *,
    conversation_entry: uuid.UUID | None | object = _DEFAULT_ENTRY,
    artifact_task_id: uuid.UUID | None = None,
) -> TaskSource:
    """真实 Task 结果来源：受理结果留下的 inbox 行 + 操作行 + 该 Task 的产物。"""
    entry = tenant.entry_id if conversation_entry is _DEFAULT_ENTRY else conversation_entry
    assert entry is None or isinstance(entry, uuid.UUID)
    conversation_id = await _seed_conversation(tenant, entry_id=entry)
    run = await _seed_run(tenant, conversation_id)
    task_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    async with runtime_db.get_session_factory()() as session:
        seq = await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=run.id,
            event_type="TOOL_RESULT_RECEIVED",
            stream_type="tool.result.received",
            payload={"event_version": 1},
        )
        await session.flush()
        event = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.tenant_id == tenant.tenant_id,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.seq == seq,
            )
        )
        assert event is not None
        operation = ToolOperation(
            tenant_id=tenant.tenant_id,
            run_id=run.id,
            actor_user_id=tenant.platform_user_id,
            source_tool_call_id=f"call-{uuid.uuid4().hex[:12]}",
            completion_mode=CompletionMode.JOIN,
            status=OperationStatus.RESULT_RECEIVED,
            task_id=task_id,
            task_snapshot_hash="sha256:" + "a" * 64,
            submission_json={},
            input_hash="sha256:" + "b" * 64,
            terminal_event_id=event.id,
            completed_at=datetime.now(UTC),
        )
        session.add(operation)
        await session.flush()
        session.add(
            ToolResultInbox(
                tenant_id=tenant.tenant_id,
                event_id=uuid.uuid4(),
                run_id=run.id,
                operation_id=operation.id,
                task_id=task_id,
                task_event_seq=1,
                payload_hash="sha256:" + "c" * 64,
                payload_json={},
                receipt_event_id=event.id,
                receipt_seq=int(seq),
            )
        )
        body = b"task result bytes"
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=None,
                task_id=artifact_task_id or task_id,
                conversation_id=conversation_id,
                artifact_type="TOOL_RESULT",
                storage_key=f"tool-results/{artifact_id}",
                media_type="text/plain",
                size=len(body),
                checksum="sha256:" + "d" * 64,
                metadata_json={"kind": "DOCUMENT", "filename": "result.txt"},
            )
        )
        await session.commit()
    return TaskSource(
        task_id=task_id, run=run, artifact_id=artifact_id, receipt_event_id=event.id
    )


def _registration_body(source: TaskSource, tenant: ActivityTenant, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task_id": str(source.task_id),
        "run_id": str(source.run.id),
        "user_id": str(tenant.platform_user_id),
        "artifact_ids": [str(source.artifact_id)],
    }
    body.update(overrides)
    return body


async def _post_task_result(
    client: AsyncClient,
    tenant: ActivityTenant,
    body: dict[str, Any],
    *,
    token: str | None | object = ...,
) -> httpx.Response:
    return await client.post(
        TASK_RESULTS_PATH, headers=_headers(tenant, token=token), json=body
    )


async def _fail_task_notification_inserts() -> None:
    """真实 DDL（隔离库）：Task 结果通知行 INSERT 一律失败（整体回滚证明）。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "CREATE FUNCTION runtime.fail_task_notification() RETURNS trigger"
                " LANGUAGE plpgsql AS $$ BEGIN IF NEW.delivery_key LIKE 'task:%' THEN"
                " RAISE EXCEPTION 'task_notification_failure'; END IF;"
                " RETURN NEW; END $$"
            )
        )
        await session.execute(
            sa.text(
                "CREATE TRIGGER fail_task_notification BEFORE INSERT"
                " ON runtime.channel_notification FOR EACH ROW"
                " EXECUTE FUNCTION runtime.fail_task_notification()"
            )
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# B-264：按用户 feed seq 增量分页
# --------------------------------------------------------------------------------------


async def test_b264_incremental_pagination_by_feed_seq_has_no_skips(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """水位推进跨页读取：每一页只出现在水位之后，合并不重不漏。"""
    tenant = activity_tenant
    conversation_id = await _seed_conversation(tenant)
    for _ in range(5):
        run = await _seed_run(tenant, conversation_id)
        await _write_notification(tenant, run.id)

    first = await _list_notifications(client, tenant, after_seq=0, page_size=2)
    assert [item["seq"] for item in first["items"]] == [1, 2]
    assert first["through_seq"] == 5 and first["next_seq"] == 2
    assert first["total"] == 5 and first["unread_count"] == 5
    assert first["page"] == 1 and first["page_size"] == 2

    second = await _list_notifications(
        client, tenant, after_seq=first["next_seq"], page_size=2
    )
    assert [item["seq"] for item in second["items"]] == [3, 4]
    assert second["next_seq"] == 4 and second["through_seq"] == 5

    third = await _list_notifications(client, tenant, after_seq=second["next_seq"], page_size=2)
    assert [item["seq"] for item in third["items"]] == [5]
    assert third["next_seq"] == third["through_seq"] == 5

    caught_up = await _list_notifications(client, tenant, after_seq=5, page_size=2)
    assert caught_up["items"] == []
    assert caught_up["next_seq"] == 5 and caught_up["unread_count"] == 5

    seen = [item["seq"] for page in (first, second, third) for item in page["items"]]
    assert seen == [1, 2, 3, 4, 5], "增量分页不得跳过或重复"

    offset = await _list_notifications(client, tenant, after_seq=0, page=2, page_size=2)
    assert [item["seq"] for item in offset["items"]] == [3, 4], "offset 页与水位语义一致"

    # 新提交只出现在既有水位之后：增量读取不回溯、不跳过
    extra_run = await _seed_run(tenant, conversation_id)
    await _write_notification(tenant, extra_run.id)
    tail = await _list_notifications(client, tenant, after_seq=5, page_size=10)
    assert [item["seq"] for item in tail["items"]] == [6]
    assert tail["through_seq"] == 6 and tail["next_seq"] == 6


async def test_b264_idle_cursor_skips_count_and_page_queries(
    client: AsyncClient, activity_tenant: ActivityTenant
) -> None:
    """[S-14/TASK-002] 空闲轮询（after_seq==through_seq）只读水位与未读，不做 count/page。"""
    tenant = activity_tenant
    conversation_id = await _seed_conversation(tenant)
    run = await _seed_run(tenant, conversation_id)
    await _write_notification(tenant, run.id)

    first = await _list_notifications(client, tenant, after_seq=0, page_size=10)
    statements: list[str] = []

    @sa.event.listens_for(runtime_db.get_engine().sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    try:
        idle = await _list_notifications(
            client, tenant, after_seq=first["through_seq"], page_size=10
        )
    finally:
        sa.event.remove(
            runtime_db.get_engine().sync_engine, "before_cursor_execute", _capture
        )

    assert idle["items"] == []
    assert idle["next_seq"] == idle["through_seq"] == first["through_seq"]
    assert idle["unread_count"] == first["unread_count"]
    assert len(statements) == 2, (
        "空闲轮询只允许水位 + 未读两条 SELECT（不再 count/page 全跑）；"
        f"实际 {len(statements)} 条：{statements}"
    )


async def test_b264_projection_is_safe_and_carries_public_refs(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """安全投影：只含公开引用与安全摘要；投递/租约/存储内部字段永不进入。"""
    tenant = activity_tenant
    conversation_id = await _seed_conversation(tenant)
    run = await _seed_run(tenant, conversation_id)
    receipt = await _write_notification(tenant, run.id)

    page = await _list_notifications(client, tenant, page_size=10)
    item = page["items"][0]
    assert set(item.keys()) == NOTIFICATION_FIELDS
    assert FORBIDDEN_FIELDS.isdisjoint(item.keys())
    assert item["id"] == str(receipt.id)
    assert item["kind"] == KIND_RUN_COMPLETED
    assert item["entry_id"] == str(tenant.entry_id)
    assert item["agent_id"] == str(tenant.agent_id)
    assert item["conversation_id"] == str(conversation_id)
    assert item["run_id"] == str(run.id)
    assert item["task_id"] is None
    assert item["summary"]
    assert item["attachments"] == []
    assert item["read_at"] is None

    raw = (await _list_notifications(client, tenant, page_size=10))
    assert "delivery_key" not in str(raw) and "storage_key" not in str(raw)


async def test_b264_committed_terminal_notification_is_incrementally_readable(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """真实终态事务提交的通知：读侧增量可见，且通知不占用 canonical seq。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    started = await _start(tenant, conversation_id, supervisor)
    events = await _drain(started)
    assert events[-1].type == "run.completed"

    page = await _list_notifications(client, tenant, after_seq=0, page_size=10)
    assert [item["kind"] for item in page["items"]] == [KIND_RUN_COMPLETED]
    item = page["items"][0]
    assert item["run_id"] == str(started.run_id)
    assert item["conversation_id"] == str(conversation_id)
    assert item["read_at"] is None and page["unread_count"] == 1
    assert page["through_seq"] == 1 and page["next_seq"] == 1

    terminal_seq = await _conversation_last_seq(conversation_id)
    assert terminal_seq == events[-1].seq, "通知不分配 canonical seq（会话水位只被事件推进）"


# --------------------------------------------------------------------------------------
# B-264：显式已读与投递状态是两条独立事实
# --------------------------------------------------------------------------------------


async def test_b264_read_is_explicit_idempotent_and_independent_from_delivery(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """已读不要求送达、送达不代替已读；重复标记幂等；只标记明确读过的行。"""
    tenant = activity_tenant
    conversation_a = await _seed_conversation(tenant)
    conversation_b = await _seed_conversation(tenant)
    run1 = await _seed_run(tenant, conversation_a)
    run2 = await _seed_run(tenant, conversation_b)
    run3 = await _seed_run(tenant, conversation_a)
    n1 = await _write_notification(tenant, run1.id)
    n2 = await _write_notification(tenant, run2.id, delivery_state=DELIVERY_PENDING)
    n3 = await _write_notification(tenant, run3.id, delivery_state=DELIVERY_PENDING)
    await _mark_delivered(n3.id)

    page = await _list_notifications(client, tenant, page_size=10)
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id[str(n3.id)]["read_at"] is None, "送达不代替已读"
    assert page["unread_count"] == 3 and page["through_seq"] == 3

    read1 = await _mark_read(client, tenant, n1.id)
    assert read1.status_code == 200, read1.text
    payload = read1.json()["data"]
    assert set(payload.keys()) == {"id", "read_at"}, "已读回执只回身份与持久时间"
    assert payload["id"] == str(n1.id) and payload["read_at"] is not None

    rows = await _notification_rows(tenant.tenant_id, tenant.platform_user_id)
    assert rows[n1.id].read_at is not None
    assert rows[n2.id].read_at is None and rows[n3.id].read_at is None
    assert rows[n1.id].delivery_state == DELIVERY_NOT_REQUIRED, "已读不碰投递回执"
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) == 3

    after_read = await _list_notifications(client, tenant, page_size=10)
    assert after_read["unread_count"] == 2
    assert [item["seq"] for item in after_read["items"]] == [1, 2, 3], (
        "读过的行仍在 feed 中（已读不改 feed 水位/不删除通知）"
    )

    repeat = await _mark_read(client, tenant, n1.id)
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["data"]["read_at"] == payload["read_at"], "重复标记幂等"
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) == 3

    read2 = await _mark_read(client, tenant, n2.id)
    assert read2.status_code == 200, read2.text
    rows = await _notification_rows(tenant.tenant_id, tenant.platform_user_id)
    assert rows[n2.id].read_at is not None
    assert rows[n2.id].delivery_state == DELIVERY_PENDING, "已读不要求送达"

    # 他用户不能标记本用户的通知（不泄露存在性）
    other_user = uuid.uuid4()
    denied = await _mark_read(client, tenant, n1.id, user_id=other_user)
    assert denied.status_code == 404 and denied.json()["code"] == "COMMON_NOT_FOUND"

    # 门控拒绝不留任何状态变化
    gated = await _mark_read(client, tenant, n3.id, token=None)
    assert gated.status_code == 403
    assert (await _notification_rows(tenant.tenant_id, tenant.platform_user_id))[n3.id].read_at is None

    # 隐藏（清理）的通知不可再被标记：唯一回执保留、投递事实不变
    await _hide_notification(n3.id)
    hidden = await _mark_read(client, tenant, n3.id)
    assert hidden.status_code == 404
    rows = await _notification_rows(tenant.tenant_id, tenant.platform_user_id)
    assert rows[n3.id].delivery_state == "DELIVERED" and rows[n3.id].delivery_result is not None
    hidden_page = await _list_notifications(client, tenant, page_size=10)
    assert [item["seq"] for item in hidden_page["items"]] == [1, 2]
    assert hidden_page["unread_count"] == 0, "n1/n2 已读、n3 隐藏 ⇒ 可见未读为 0（隐藏行不打扰用户）"


# --------------------------------------------------------------------------------------
# B-264：受信 Task 结果入箱端口
# --------------------------------------------------------------------------------------


async def test_b264_task_result_inbox_port_verifies_source_and_persists_before_ack(
    client: AsyncClient, activity_tenant: ActivityTenant, activity_db: dict[str, str]
):
    """核真实来源后持久入箱才回 persisted；重投命中原回执；投影只含安全引用。"""
    tenant = activity_tenant
    source = await _seed_task_source(tenant)

    response = await _post_task_result(client, tenant, _registration_body(source, tenant))
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["persisted"] is True and receipt["duplicate"] is False
    assert receipt["task_id"] == str(source.task_id) and receipt["seq"] == 1

    # 独立连接验证：响应返回时行已提交（持久入箱后才回 persisted）
    side_engine = create_async_engine(activity_db["database_url"])
    try:
        async with side_engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.text(
                        "SELECT id, seq, kind, task_id, delivery_key, delivery_state, read_at,"
                        " artifact_ids, event_id FROM runtime.channel_notification"
                        " WHERE tenant_id = :tenant AND task_id = :task"
                    ),
                    {"tenant": tenant.tenant_id, "task": source.task_id},
                )
            ).one()
    finally:
        await side_engine.dispose()
    assert row.id == uuid.UUID(receipt["notification_id"]) and row.seq == 1
    assert row.kind == KIND_TASK_RESULT
    assert row.delivery_key == task_result_delivery_key(source.task_id)
    assert row.delivery_state == DELIVERY_NOT_REQUIRED and row.read_at is None
    assert [uuid.UUID(str(value)) for value in row.artifact_ids] == [source.artifact_id]
    assert row.event_id == source.receipt_event_id
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) == 1

    page = await _list_notifications(client, tenant, page_size=10)
    item = page["items"][0]
    assert item["kind"] == KIND_TASK_RESULT
    assert item["task_id"] == str(source.task_id)
    assert item["event_id"] == str(source.receipt_event_id)
    assert item["read_at"] is None and page["unread_count"] == 1
    assert [attachment["artifact_id"] for attachment in item["attachments"]] == [
        str(source.artifact_id)
    ]
    assert item["attachments"][0]["available"] is True
    assert set(item["attachments"][0].keys()) == {
        "artifact_id",
        "available",
        "kind",
        "media_type",
        "size",
        "filename",
        "checksum",
    }
    assert FORBIDDEN_FIELDS.isdisjoint(item.keys())

    replay = await _post_task_result(client, tenant, _registration_body(source, tenant))
    assert replay.status_code == 200, replay.text
    replayed = replay.json()["data"]
    assert replayed["duplicate"] is True and replayed["persisted"] is True
    assert replayed["notification_id"] == receipt["notification_id"]
    assert replayed["seq"] == receipt["seq"]
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) == 1
    assert len(await _notification_rows(tenant.tenant_id, tenant.platform_user_id)) == 1


async def test_b264_task_result_port_rejects_fabricated_sources(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """伪造 Task/Run/用户/入口/产物来源一律拒绝，且不落任何通知行。"""
    tenant = activity_tenant
    source = await _seed_task_source(tenant)
    other_user = uuid.uuid4()

    unknown_task = await _post_task_result(
        client,
        tenant,
        _registration_body(source, tenant, task_id=str(uuid.uuid4())),
    )
    assert unknown_task.status_code == 403
    assert unknown_task.json()["code"] == "TOOL_RESULT_BINDING_MISMATCH"

    wrong_run = await _post_task_result(
        client, tenant, _registration_body(source, tenant, run_id=str(uuid.uuid4()))
    )
    assert wrong_run.status_code == 403

    wrong_user = await _post_task_result(
        client, tenant, _registration_body(source, tenant, user_id=str(other_user))
    )
    assert wrong_user.status_code == 403

    # 产物必须属于该 Task（调用方自报的附件引用没有位置）
    foreign_artifact = await _seed_task_source(tenant, artifact_task_id=uuid.uuid4())
    foreign = await _post_task_result(
        client,
        tenant,
        _registration_body(source, tenant, artifact_ids=[str(foreign_artifact.artifact_id)]),
    )
    assert foreign.status_code == 403

    # 无入口归属的会话不进网页收件箱
    unattributed = await _seed_task_source(tenant, conversation_entry=None)
    missing_entry = await _post_task_result(
        client, tenant, _registration_body(unattributed, tenant, artifact_ids=[])
    )
    assert missing_entry.status_code == 404
    assert missing_entry.json()["code"] == "COMMON_NOT_FOUND"

    # 服务门控：缺失/错误身份一律 403
    for token in (None, "wrong-token"):
        denied = await _post_task_result(
            client, tenant, _registration_body(source, tenant), token=token
        )
        assert denied.status_code == 403
    assert await _notification_rows(tenant.tenant_id, tenant.platform_user_id) == {}
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) is None


async def test_b264_task_result_write_failure_never_acknowledges_persistence(
    activity_tenant: ActivityTenant,
):
    """通知写不进去 ⇒ 不产生 persisted 回执：feed 不推进、无通知行（整体回滚）。"""
    tenant = activity_tenant
    source = await _seed_task_source(tenant)
    await _fail_task_notification_inserts()

    async with runtime_db.get_session_factory()() as session:
        with pytest.raises(sa.exc.DBAPIError):
            await ChannelNotificationQueryService(session).register_task_result(
                tenant_id=tenant.tenant_id,
                registration=WebchatTaskResultRegistration(
                    task_id=source.task_id,
                    run_id=source.run.id,
                    user_id=tenant.platform_user_id,
                    artifact_ids=[source.artifact_id],
                ),
            )

    assert await _notification_rows(tenant.tenant_id, tenant.platform_user_id) == {}
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) is None


# --------------------------------------------------------------------------------------
# B-03：通知 seq 按用户提交顺序推进
# --------------------------------------------------------------------------------------


async def _write_one(run_id: uuid.UUID, delivery_key: str) -> RunNotificationReceipt:
    async with runtime_db.get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        receipt = await write_run_notification(
            session, run=run, kind=KIND_RUN_COMPLETED, delivery_key=delivery_key
        )
        assert receipt is not None
        await session.commit()
        return receipt


async def test_b03_notification_seq_advances_in_user_commit_order(
    activity_tenant: ActivityTenant,
):
    """逐条提交按序推进、无缺口；不同用户各自独立；并发提交仍无重复序号。"""
    tenant = activity_tenant
    other_user = uuid.uuid4()
    conversation_a = await _seed_conversation(tenant)
    conversation_b = await _seed_conversation(tenant, user_id=other_user)
    runs = [await _seed_run(tenant, conversation_a) for _ in range(5)]
    other_run = await _seed_run(tenant, conversation_b, user_id=other_user)

    ordered = [
        await _write_one(run.id, f"order:{index}") for index, run in enumerate(runs[:3])
    ]
    assert [receipt.seq for receipt in ordered] == [1, 2, 3]
    other = await _write_one(other_run.id, "order:other")
    assert other.seq == 1, "不同用户的 feed seq 各自独立"

    concurrent = await asyncio.gather(
        _write_one(runs[3].id, "order:4"), _write_one(runs[4].id, "order:5")
    )
    assert sorted(receipt.seq for receipt in concurrent) == [4, 5]
    assert await _feed_last_seq(tenant.tenant_id, tenant.platform_user_id) == 5
    assert await _feed_last_seq(tenant.tenant_id, other_user) == 1

    rows = await _notification_rows(tenant.tenant_id, tenant.platform_user_id)
    assert sorted(row.seq for row in rows.values()) == [1, 2, 3, 4, 5], "序号连续无缺口"
    assert len({row.seq for row in rows.values()}) == 5, "并发提交不得重复占用序号"


# --------------------------------------------------------------------------------------
# B-03：事件追尾与历史分页无跳过；读游标不能跨会话
# --------------------------------------------------------------------------------------


async def test_b03_event_tailing_and_history_pagination_do_not_skip(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """冻结水位的消息分页无跳过/重复；Run 事件追尾按 seq 严格递增；新提交不移动旧水位。"""
    tenant = activity_tenant
    supervisor = ExecutionSupervisor()
    conversation_id = await _seed_conversation(tenant)
    started = await _start(tenant, conversation_id, supervisor)
    await _drain(started)

    watermark = await _conversation_last_seq(conversation_id)
    visible = await _visible_message_rows(conversation_id)
    assert [row.event_type for row in visible] == ["USER_MESSAGE", "ASSISTANT_MESSAGE"]

    seen: list[dict[str, Any]] = []
    page = 1
    while True:
        response = await client.get(
            MESSAGES_PATH.format(conversation_id=conversation_id),
            headers=_headers(tenant),
            params={
                "user_id": str(tenant.platform_user_id),
                "agent_id": str(tenant.agent_id),
                "entry_id": str(tenant.entry_id),
                "page": page,
                "page_size": 1,
                "through_seq": watermark,
            },
        )
        assert response.status_code == 200, response.text
        view = response.json()["data"]
        assert view["through_seq"] == watermark, "分页期间水位固定"
        seen.extend(view["items"])
        if not view["items"] or len(seen) >= view["total"]:
            break
        page += 1
    assert sorted(item["seq"] for item in seen) == [row.seq for row in visible]
    assert len({item["event_id"] for item in seen}) == len(seen), "历史分页不得重复"
    assert {item["event_id"] for item in seen} == {str(row.id) for row in visible}

    async with _service(tenant, supervisor) as service:
        replay = await service.tail_run_events(started.run_id, tenant.tenant_id)
    events = await _drain(replay)
    tail_seqs = [event.seq for event in events]
    assert tail_seqs == sorted(tail_seqs) and len(set(tail_seqs)) == len(tail_seqs)
    assert events[-1].type == "run.completed"
    stream_rows = await _run_stream_rows(started.run_id)
    assert tail_seqs == [row.seq for row in stream_rows], "追尾无跳过：与持久流事件逐一对应"

    # 新一轮提交不移动已冻结水位，也不让旧页出现跳过/重复
    second = await _start(tenant, conversation_id, supervisor)
    await _drain(second)
    frozen = await client.get(
        MESSAGES_PATH.format(conversation_id=conversation_id),
        headers=_headers(tenant),
        params={
            "user_id": str(tenant.platform_user_id),
            "agent_id": str(tenant.agent_id),
            "entry_id": str(tenant.entry_id),
            "page": 1,
            "page_size": 10,
            "through_seq": watermark,
        },
    )
    assert frozen.status_code == 200, frozen.text
    frozen_view = frozen.json()["data"]
    assert frozen_view["through_seq"] == watermark
    assert [item["event_id"] for item in frozen_view["items"]] == [
        str(row.id) for row in visible
    ], "冻结水位不受新提交影响"


async def test_b03_read_cursors_cannot_cross_conversations_or_users(
    client: AsyncClient, activity_tenant: ActivityTenant
):
    """会话消息水位与用户 feed 水位都只在自己流内有效：跨会话/跨用户显式 CURSOR_AHEAD。"""
    tenant = activity_tenant
    other_user = uuid.uuid4()
    conversation_a = await _seed_conversation(tenant)
    conversation_b = await _seed_conversation(tenant)
    run_a = await _seed_run(tenant, conversation_a)
    run_b = await _seed_run(tenant, conversation_b)
    await _write_notification(tenant, run_a.id)
    await _write_notification(tenant, run_a.id, delivery_key="cross:second")
    await _write_notification(tenant, run_b.id, delivery_key="cross:third")
    async with runtime_db.get_session_factory()() as session:
        seq = await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_a,
            run_id=run_a.id,
            event_type="USER_MESSAGE",
            payload={"text": "会话 A 的消息"},
        )
        await session.commit()
    a_watermark = int(seq)
    assert a_watermark > await _conversation_last_seq(conversation_b)

    crossed = await client.get(
        MESSAGES_PATH.format(conversation_id=conversation_b),
        headers=_headers(tenant),
        params={
            "user_id": str(tenant.platform_user_id),
            "agent_id": str(tenant.agent_id),
            "entry_id": str(tenant.entry_id),
            "page": 1,
            "page_size": 10,
            "through_seq": a_watermark,
        },
    )
    assert crossed.status_code == 400, crossed.text
    assert crossed.json()["code"] == "CURSOR_AHEAD", "会话读游标不得跨会话"

    own = await client.get(
        MESSAGES_PATH.format(conversation_id=conversation_b),
        headers=_headers(tenant),
        params={
            "user_id": str(tenant.platform_user_id),
            "agent_id": str(tenant.agent_id),
            "entry_id": str(tenant.entry_id),
            "page": 1,
            "page_size": 10,
        },
    )
    assert own.status_code == 200, own.text
    own_view = own.json()["data"]
    assert {item["conversation_id"] for item in own_view["items"]} <= {str(conversation_b)}

    # 用户 feed 游标只在自己流内有效：他用户的水位在本用户 feed 上显式拒绝
    feed_a = await _list_notifications(client, tenant, page_size=10)
    assert feed_a["through_seq"] == 3
    crossed_user = await client.get(
        NOTIFICATIONS_PATH,
        headers=_headers(tenant),
        params={
            "user_id": str(other_user),
            "after_seq": feed_a["through_seq"],
            "page": 1,
            "page_size": 10,
        },
    )
    assert crossed_user.status_code == 400, crossed_user.text
    assert crossed_user.json()["code"] == "CURSOR_AHEAD", "活动游标不得跨用户"

    empty = await _list_notifications(client, tenant, user_id=other_user, page_size=10)
    assert empty["items"] == [] and empty["unread_count"] == 0
