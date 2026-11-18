"""TASK-004：Task 成功终态的 `task.message_outbox` 与 Runtime 受理面。

真实边界：真实 PostgreSQL（终态事务、唯一约束、Schedule/Revision/Route 行）、真实
`write_terminal`、真实 `TaskMessageOutboxLoop`。Runtime 一侧用两种真实程度覆盖：

- 端点契约：真实 Runtime ASGI + 真实 `message_delivery` 表（幂等、owner/附件/路由校验）；
- 消费循环：脚本化 Runtime 客户端，只替换 HTTP 传输，状态仍全部落真库。

失败/取消不写 outbox、NONE 不写、无附件保留既有文本投递、路由失效置
`NEEDS_ATTENTION`、重放幂等都在这里。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import Artifact, MessageDelivery
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.delivery.message_outbox import (
    OUTBOX_FAILED,
    TaskMessageOutboxLoop,
)
from muad_agent_worker.delivery.message_outbox_client import (
    MessageOutboxReceipt,
    MessageOutboxRejected,
)
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskMessageOutbox,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_contracts import (
    AttachmentRef,
    DeliveryRouteInput,
    MessageDeliveryEnqueueRequest,
    TerminalStatus,
    delivery_target_key,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_task, internal_service_headers, persist_task

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
BOT_ID = "bot-task-outbox"
EXTERNAL_USER_ID = "wotv-task-outbox"


@dataclass(frozen=True, slots=True)
class _Tenant:
    context: TenantContext
    runtime_sessions: async_sessionmaker[AsyncSession]


@pytest.fixture
async def outbox_tenant(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[_Tenant]:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    await runtime_db.dispose_engine()
    try:
        yield _Tenant(context=tenant, runtime_sessions=runtime_db.get_session_factory())
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
async def runtime_client(outbox_tenant: _Tenant) -> AsyncIterator[AsyncClient]:
    from muad_agent_runtime.main import app as runtime_app

    async with AsyncClient(
        transport=ASGITransport(app=runtime_app), base_url="http://runtime"
    ) as http_client:
        yield http_client


def _sample_route() -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
    )


async def _seed_route(
    tenant: TenantContext,
    *,
    platform_user_id: uuid.UUID,
    status: str = "ACTIVE",
) -> uuid.UUID:
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant.tenant_id,
            platform_user_id=platform_user_id,
            route=_sample_route(),
        )
        if status != "ACTIVE":
            await session.execute(
                sa.update(DeliveryRoute)
                .where(DeliveryRoute.id == route_id)
                .values(status=status)
            )
    return route_id


async def _seed_schedule(
    tenant: TenantContext,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    intent: str = "PUSH",
    route_id: uuid.UUID | None = None,
    status: str = "ACTIVE",
) -> tuple[TaskSchedule, TaskScheduleRevision]:
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            owner_user_id=owner,
            agent_id=agent,
            name="多文件周报",
            status=status,
            config_version=1,
            next_fire_at=datetime.now(UTC) + timedelta(days=1),
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            schedule_id=schedule.id,
            revision_no=1,
            mode="SKILL",
            instruction_raw="生成多文件周报",
            normalized_goal="生成周报",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone=TZ,
            payload_kind="SKILL_PLAN",
            payload_json={
                "kind": "SKILL_PLAN",
                "skill_id": str(uuid.uuid4()),
                "input": {},
                "argv": [],
                "window": {"kind": "DAY", "offset": 0},
            },
            delivery_intent=intent,
            delivery_route_id=route_id,
            created_by=owner,
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
    return schedule, revision


async def _seed_execution(
    tenant: TenantContext,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    status: str = "QUEUED",
) -> TaskExecution:
    return await persist_task(
        tenant,
        schedule_id=schedule.id,
        schedule_revision_id=revision.id,
        trigger_type="SCHEDULED",
        agent_id=agent,
        actor_user_id=owner,
        status=status,
        result_json={"summary": "周报已生成"},
    )


async def _seed_artifact(
    tenant: _Tenant,
    task_id: uuid.UUID,
    *,
    filename: str,
    content: bytes,
) -> AttachmentRef:
    checksum = "sha256:" + hashlib.sha256(content + filename.encode()).hexdigest()
    async with tenant.runtime_sessions() as session, session.begin():
        artifact = Artifact(
            id=uuid.uuid4(),
            tenant_id=tenant.context.tenant_id,
            run_id=None,
            task_id=task_id,
            artifact_type="OUTPUT",
            storage_key=f"tasks/{tenant.context.tenant_id}/{task_id}/{uuid.uuid4()}/{filename}",
            media_type="text/plain",
            size=len(content),
            checksum=checksum,
            metadata_json={"filename": filename, "kind": "DOCUMENT"},
        )
        session.add(artifact)
        await session.flush()
        return AttachmentRef(
            storage_key=artifact.storage_key,
            kind="DOCUMENT",
            media_type=artifact.media_type,
            size=artifact.size,
            filename=filename,
            checksum=artifact.checksum,
            artifact_id=artifact.id,
        )


async def _finish(
    tenant: TenantContext, task_id: uuid.UUID, status: TerminalStatus
) -> bool:
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskExecution, task_id)
        assert row is not None
        return await write_terminal(
            session,
            row,
            TerminalChange(status=status, result={"summary": "周报已生成"}),
            datetime.now(UTC),
        )


async def _outbox_rows(
    tenant: TenantContext, task_id: uuid.UUID
) -> list[TaskMessageOutbox]:
    async with tenant.session_factory() as session, session.begin():
        rows = await session.scalars(
            select(TaskMessageOutbox).where(
                TaskMessageOutbox.tenant_id == tenant.tenant_id,
                TaskMessageOutbox.task_id == task_id,
            )
        )
    return list(rows)


async def _task_deliveries(tenant: TenantContext, task_id: uuid.UUID) -> list[TaskDelivery]:
    async with tenant.session_factory() as session, session.begin():
        rows = await session.scalars(
            select(TaskDelivery).where(
                TaskDelivery.tenant_id == tenant.tenant_id,
                TaskDelivery.execution_id == task_id,
            )
        )
    return list(rows)


async def _schedule_row(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskSchedule, schedule_id)
    assert row is not None
    return row


async def _outbox_by_id(
    tenant: TenantContext, outbox_id: uuid.UUID
) -> TaskMessageOutbox:
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskMessageOutbox, outbox_id)
    assert row is not None
    return row


class _FakeRuntimeInbox:
    """Runtime 受理面的幂等替代：同 (message_key, target_key) 只保留一条。"""

    def __init__(self) -> None:
        self.requests: list[MessageDeliveryEnqueueRequest] = []
        self.by_key: dict[tuple[str, str], uuid.UUID] = {}

    async def enqueue(
        self, *, tenant_id: str, request: MessageDeliveryEnqueueRequest
    ) -> MessageOutboxReceipt:
        self.requests.append(request)
        key = (request.message_key, request.target_key)
        duplicate = key in self.by_key
        delivery_id = self.by_key.setdefault(key, uuid.uuid4())
        return MessageOutboxReceipt(
            delivery_id=delivery_id, status="PENDING", duplicate=duplicate
        )


class _RejectingRuntime:
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self._code = code
        self._retryable = retryable

    async def enqueue(
        self, *, tenant_id: str, request: MessageDeliveryEnqueueRequest
    ) -> MessageOutboxReceipt:
        raise MessageOutboxRejected(self._code, retryable=self._retryable)


def _outbox_loop(
    tenant: TenantContext, client: Any
) -> TaskMessageOutboxLoop:
    return TaskMessageOutboxLoop(
        tenant.session_factory,
        client,
        tenant.settings,
        lease_owner="test-outbox",
    )


async def test_completed_task_with_push_route_freezes_multi_attachment_message(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    attachment_a = await _seed_artifact(
        outbox_tenant, execution.id, filename="a.txt", content=b"a"
    )
    attachment_b = await _seed_artifact(
        outbox_tenant, execution.id, filename="b.txt", content=b"bb"
    )

    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True

    rows = await _outbox_rows(tenant, execution.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "PENDING"
    assert row.message_key == f"execution:{execution.id}:final"
    assert row.kind == "TASK_RESULT"
    assert row.target_key == delivery_target_key(_sample_route())
    assert row.payload_json["text"] == "周报已生成"
    assert [item["artifact_id"] for item in row.payload_json["attachments"]] == [
        str(attachment_a.artifact_id),
        str(attachment_b.artifact_id),
    ]
    assert await _task_deliveries(tenant, execution.id) == []
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"


async def test_failed_and_cancelled_terminals_never_write_outbox(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    for status in (TerminalStatus.FAILED, TerminalStatus.CANCELLED):
        owner, agent = uuid.uuid4(), uuid.uuid4()
        route_id = await _seed_route(tenant, platform_user_id=owner)
        schedule, revision = await _seed_schedule(
            tenant, owner=owner, agent=agent, route_id=route_id
        )
        execution = await _seed_execution(
            tenant, schedule, revision, owner=owner, agent=agent
        )
        await _seed_artifact(
            outbox_tenant, execution.id, filename=f"{status}.txt", content=b"x"
        )

        assert await _finish(tenant, execution.id, status) is True

        assert await _outbox_rows(tenant, execution.id) == []


async def test_none_intent_never_writes_outbox_and_keeps_web_pull(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, intent="NONE", route_id=None
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    await _seed_artifact(outbox_tenant, execution.id, filename="web.txt", content=b"x")

    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True

    assert await _outbox_rows(tenant, execution.id) == []
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"


async def test_immediate_task_with_attachments_hands_over_to_outbox(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    execution = await persist_task(
        tenant,
        agent_id=agent,
        actor_user_id=owner,
        result_json={"summary": "立即任务完成"},
    )
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskDelivery(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                execution_id=execution.id,
                delivery_route_id=route_id,
                delivery_kind="PUSH",
                delivery_key=f"execution:{execution.id}:final",
                status="PENDING",
                attempt_count=0,
            )
        )
    await _seed_artifact(
        outbox_tenant, execution.id, filename="immediate.txt", content=b"now"
    )

    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True

    rows = await _outbox_rows(tenant, execution.id)
    assert len(rows) == 1
    deliveries = await _task_deliveries(tenant, execution.id)
    assert [(row.delivery_kind, row.status) for row in deliveries] == [("PUSH", "CANCELLED")]


async def test_task_without_attachments_keeps_existing_text_delivery(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )

    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True

    assert await _outbox_rows(tenant, execution.id) == []
    assert len(await _task_deliveries(tenant, execution.id)) == 1


async def test_child_task_never_writes_outbox_or_direct_delivery(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    parent = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    child = await persist_task(
        tenant,
        parent_id=parent.id,
        root_id=parent.id,
        schedule_id=schedule.id,
        schedule_revision_id=revision.id,
        trigger_type="SCHEDULED",
        agent_id=agent,
        actor_user_id=owner,
        item_key="customer-a",
        result_json={"summary": "子任务完成"},
    )
    await _seed_artifact(
        outbox_tenant, child.id, filename="child.txt", content=b"child"
    )

    assert await _finish(tenant, child.id, TerminalStatus.COMPLETED) is True

    assert await _outbox_rows(tenant, child.id) == []
    assert await _task_deliveries(tenant, child.id) == []


async def test_outbox_consumer_applies_and_replay_keeps_one_message(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    await _seed_artifact(outbox_tenant, execution.id, filename="report.txt", content=b"ok")
    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True
    row = (await _outbox_rows(tenant, execution.id))[0]

    inbox = _FakeRuntimeInbox()
    loop = _outbox_loop(tenant, inbox)
    first = await loop.run_once()
    assert first is not None and first.applied is True
    assert (await _outbox_by_id(tenant, row.id)).status == "APPLIED"
    assert len(inbox.by_key) == 1

    # 模拟“Runtime 已受理、进程在 mark APPLIED 前崩溃”：行回到 PENDING 再消费一次。
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskMessageOutbox)
            .where(TaskMessageOutbox.id == row.id)
            .values(
                status="PENDING",
                lease_owner=None,
                lease_until=None,
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )

    second = await loop.run_once()
    assert second is not None and second.applied is True
    assert (await _outbox_by_id(tenant, row.id)).status == "APPLIED"
    assert len(inbox.by_key) == 1
    assert len(inbox.requests) == 2
    assert inbox.requests[0].message_key == inbox.requests[1].message_key
    assert inbox.requests[0].target_key == inbox.requests[1].target_key


async def test_route_failure_marks_outbox_failed_and_schedule_needs_attention(
    outbox_tenant: _Tenant,
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    await _seed_artifact(outbox_tenant, execution.id, filename="report.txt", content=b"ok")
    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True
    row = (await _outbox_rows(tenant, execution.id))[0]

    loop = _outbox_loop(
        tenant, _RejectingRuntime("SCHEDULE_DELIVERY_UNSUPPORTED", retryable=False)
    )
    outcome = await loop.run_once()
    assert outcome is not None and outcome.applied is False
    stored = await _outbox_by_id(tenant, row.id)
    assert stored.status == OUTBOX_FAILED
    assert stored.last_error_code == "SCHEDULE_DELIVERY_UNSUPPORTED"

    refreshed = await _schedule_row(tenant, schedule.id)
    assert refreshed.status == "NEEDS_ATTENTION"
    assert refreshed.attention_reason == "SCHEDULE_DELIVERY_UNSUPPORTED"
    assert refreshed.next_fire_at is None
    async with tenant.session_factory() as session, session.begin():
        events = (
            await session.scalars(
                select(TaskEvent).where(
                    TaskEvent.tenant_id == tenant.tenant_id,
                    TaskEvent.entity_type == "SCHEDULE",
                    TaskEvent.entity_id == schedule.id,
                )
            )
        ).all()
    assert [event.event_type for event in events] == ["NEEDS_ATTENTION"]


async def test_transport_failure_backs_off_without_failing_outbox(
    outbox_tenant: _Tenant,
) -> None:
    from muad_agent_worker.delivery.message_outbox_client import MessageOutboxTransportError

    class _UnavailableRuntime:
        async def enqueue(self, *, tenant_id: str, request: Any) -> Any:
            raise MessageOutboxTransportError("connection reset")

    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner)
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent
    )
    await _seed_artifact(outbox_tenant, execution.id, filename="report.txt", content=b"ok")
    assert await _finish(tenant, execution.id, TerminalStatus.COMPLETED) is True
    row = (await _outbox_rows(tenant, execution.id))[0]

    loop = _outbox_loop(tenant, _UnavailableRuntime())
    outcome = await loop.run_once()
    assert outcome is not None and outcome.applied is False
    stored = await _outbox_by_id(tenant, row.id)
    assert stored.status == "PENDING"
    assert stored.last_error_code == "MESSAGE_DELIVERY_TRANSPORT_ERROR"
    assert stored.next_attempt_at > datetime.now(UTC)


async def test_runtime_message_delivery_endpoint_is_idempotent(
    outbox_tenant: _Tenant, runtime_client: AsyncClient
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    await _seed_route(tenant, platform_user_id=owner)
    # 直接种一条已完成执行（端点只认成功终态）。
    async with tenant.session_factory() as session, session.begin():
        execution = TaskExecution(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            agent_id=agent,
            actor_user_id=owner,
            trigger_type="SCHEDULED",
            task_type="SKILL",
            status="COMPLETED",
            input_json={},
            execution_snapshot_schema_version=4,
            execution_snapshot_json={"schema_version": 4},
            snapshot_hash="sha256:" + "b" * 64,
            idempotency_key=f"api-{uuid.uuid4()}",
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
            finished_at=datetime.now(UTC),
        )
        session.add(execution)
        await session.flush()
        execution_id = execution.id
    attachment = await _seed_artifact(
        outbox_tenant, execution_id, filename="api.txt", content=b"api"
    )
    request = MessageDeliveryEnqueueRequest(
        tenant_id=tenant.tenant_id,
        owner_type="TASK",
        owner_id=execution_id,
        message_key=f"execution:{execution_id}:final",
        kind="TASK_RESULT",
        target_key=delivery_target_key(_sample_route()),
        route=_sample_route(),
        payload={"text": "完成", "attachments": [attachment]},
    )
    headers = internal_service_headers(tenant.tenant_id)

    first = await runtime_client.post(
        "/internal/message-deliveries",
        json=request.model_dump(mode="json"),
        headers=headers,
    )
    second = await runtime_client.post(
        "/internal/message-deliveries",
        json=request.model_dump(mode="json"),
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["delivery_id"] == second.json()["data"]["delivery_id"]
    assert second.json()["data"]["duplicate"] is True
    async with outbox_tenant.runtime_sessions() as session:
        stored = (
            await session.scalars(
                select(MessageDelivery).where(
                    MessageDelivery.tenant_id == tenant.tenant_id,
                    MessageDelivery.message_key == request.message_key,
                )
            )
        ).all()
    assert len(stored) == 1
    assert stored[0].target_key == request.target_key
    assert stored[0].payload_json["attachments"][0]["artifact_id"] == str(
        attachment.artifact_id
    )


async def test_runtime_message_delivery_endpoint_rejects_revoked_route(
    outbox_tenant: _Tenant, runtime_client: AsyncClient
) -> None:
    tenant = outbox_tenant.context
    owner, agent = uuid.uuid4(), uuid.uuid4()
    route_id = await _seed_route(tenant, platform_user_id=owner, status="DISABLED")
    schedule, revision = await _seed_schedule(
        tenant, owner=owner, agent=agent, route_id=route_id
    )
    execution = await _seed_execution(
        tenant, schedule, revision, owner=owner, agent=agent, status="COMPLETED"
    )
    attachment = await _seed_artifact(
        outbox_tenant, execution.id, filename="revoked.txt", content=b"x"
    )
    request = MessageDeliveryEnqueueRequest(
        tenant_id=tenant.tenant_id,
        owner_type="TASK",
        owner_id=execution.id,
        message_key=f"execution:{execution.id}:final",
        kind="TASK_RESULT",
        target_key=delivery_target_key(_sample_route()),
        route=_sample_route(),
        payload={"text": "完成", "attachments": [attachment]},
    )

    response = await runtime_client.post(
        "/internal/message-deliveries",
        json=request.model_dump(mode="json"),
        headers=internal_service_headers(tenant.tenant_id),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_DELIVERY_UNSUPPORTED"
    async with outbox_tenant.runtime_sessions() as session:
        count = await session.scalar(
            select(sa.func.count()).select_from(MessageDelivery).where(
                MessageDelivery.tenant_id == tenant.tenant_id
            )
        )
    assert count == 0


async def test_service_identity_is_required() -> None:
    """端点不接受匿名调用：没有服务身份时不进入业务校验。"""
    from muad_agent_runtime.main import app as runtime_app

    async with AsyncClient(
        transport=ASGITransport(app=runtime_app), base_url="http://runtime"
    ) as client:
        response = await client.post(
            "/internal/message-deliveries",
            json={
                "tenant_id": "any",
                "owner_type": "TASK",
                "owner_id": str(uuid.uuid4()),
                "message_key": f"execution:{uuid.uuid4()}:final",
                "kind": "TASK_RESULT",
                "target_key": "WECOM:" + "a" * 48,
                "route": _sample_route().model_dump(mode="json"),
                "payload": {"text": "x", "attachments": []},
            },
        )
    assert response.status_code == 403
