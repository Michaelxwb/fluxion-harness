"""TASK-004：任务中心产物投递状态（`TaskArtifactRef.deliveries[]`）。

真实边界：真实 Worker ASGI 读面 + 真实 PostgreSQL（`task.message_outbox`、
`runtime.message_delivery`、`runtime.artifact`）。断言只关心任务中心读出来的事实：

- outbox PENDING/FAILED 必须显示 `PENDING`/`FAILED`，不能退化成“仅 Web”；
- `delivery_intent=NONE` 时 `deliveries=[]`；
- 已受理的 outbox 以 `message_delivery` 的消息级状态为准（含 DEGRADED 取件链接）；
- `target_label` 只显示安全通道标签，不暴露内部路由 ID。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import Artifact, MessageDelivery
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import (
    TaskExecution,
    TaskMessageOutbox,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_contracts import AttachmentRef, DeliveryRouteInput, delivery_target_key
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from console_platform.conftest import TenantContext
from tests.internal_service import TOKEN as INTERNAL_TOKEN

pytestmark = pytest.mark.integration

BOT_ID = "bot-task-center"
EXTERNAL_USER_ID = "wotv-task-center"
TARGET_KEY = delivery_target_key(
    DeliveryRouteInput(channel="WECOM", bot_id=BOT_ID, external_user_id=EXTERNAL_USER_ID)
)
ROUTE_JSON = DeliveryRouteInput(
    channel="WECOM", bot_id=BOT_ID, external_user_id=EXTERNAL_USER_ID
).model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class _TaskTree:
    execution_id: uuid.UUID
    schedule_id: uuid.UUID
    revision_id: uuid.UUID
    owner_id: uuid.UUID
    agent_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _Center:
    tenant_id: str
    sessions: async_sessionmaker[AsyncSession]
    runtime_sessions: async_sessionmaker[AsyncSession]
    client: AsyncClient


@pytest.fixture
async def task_center(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> AsyncIterator[_Center]:
    from muad_agent_worker.main import app as worker_app

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    await runtime_db.dispose_engine()
    async with AsyncClient(
        transport=ASGITransport(app=worker_app), base_url="http://worker"
    ) as client:
        yield _Center(
            tenant_id=tenant.tenant_id,
            sessions=worker_session_factory(),
            runtime_sessions=runtime_db.get_session_factory(),
            client=client,
        )
    await runtime_db.dispose_engine()


def _headers(center: _Center, tree: _TaskTree) -> dict[str, str]:
    return {
        "X-Internal-Service": INTERNAL_TOKEN,
        "X-Tenant-Id": center.tenant_id,
        "X-Actor-User-Id": str(tree.owner_id),
    }


async def _seed_task(
    center: _Center, *, intent: str = "PUSH"
) -> _TaskTree:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    schedule_id, revision_id, execution_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    async with center.sessions() as session, session.begin():
        route_id = None
        if intent == "PUSH":
            route_id = await upsert_delivery_route(
                session,
                tenant_id=center.tenant_id,
                platform_user_id=owner,
                route=DeliveryRouteInput(
                    channel="WECOM", bot_id=BOT_ID, external_user_id=EXTERNAL_USER_ID
                ),
            )
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=center.tenant_id,
            owner_user_id=owner,
            agent_id=agent,
            name="任务中心投递",
            status="ACTIVE",
            config_version=1,
            next_fire_at=now + timedelta(days=1),
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=revision_id,
            tenant_id=center.tenant_id,
            schedule_id=schedule_id,
            revision_no=1,
            mode="SKILL",
            instruction_raw="生成产物",
            normalized_goal="生成产物",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
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
        schedule.current_revision_id = revision_id
        session.add(
            TaskExecution(
                id=execution_id,
                tenant_id=center.tenant_id,
                schedule_id=schedule_id,
                schedule_revision_id=revision_id,
                agent_id=agent,
                actor_user_id=owner,
                task_type="SKILL",
                trigger_type="SCHEDULED",
                status="COMPLETED",
                input_json={},
                execution_snapshot_schema_version=4,
                execution_snapshot_json={"schema_version": 4},
                snapshot_hash="sha256:" + "c" * 64,
                idempotency_key=f"center-{execution_id}",
                deadline_at=now + timedelta(hours=1),
                finished_at=now,
            )
        )
    return _TaskTree(
        execution_id=execution_id,
        schedule_id=schedule_id,
        revision_id=revision_id,
        owner_id=owner,
        agent_id=agent,
    )


async def _seed_child(center: _Center, tree: _TaskTree, *, item_key: str) -> uuid.UUID:
    child_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with center.sessions() as session, session.begin():
        session.add(
            TaskExecution(
                id=child_id,
                tenant_id=center.tenant_id,
                parent_id=tree.execution_id,
                root_id=tree.execution_id,
                item_key=item_key,
                schedule_id=tree.schedule_id,
                schedule_revision_id=tree.revision_id,
                agent_id=tree.agent_id,
                actor_user_id=tree.owner_id,
                task_type="SKILL",
                trigger_type="SCHEDULED",
                status="COMPLETED",
                input_json={},
                execution_snapshot_schema_version=4,
                execution_snapshot_json={"schema_version": 4},
                snapshot_hash="sha256:" + "d" * 64,
                idempotency_key=f"child-{child_id}",
                deadline_at=now + timedelta(hours=1),
                finished_at=now,
            )
        )
    return child_id


async def _seed_artifact(
    center: _Center, tree: _TaskTree, *, filename: str
) -> AttachmentRef:
    return await _seed_artifact_for_task(
        center, tree, task_id=tree.execution_id, filename=filename
    )


async def _seed_artifact_for_task(
    center: _Center, tree: _TaskTree, *, task_id: uuid.UUID, filename: str
) -> AttachmentRef:
    content = filename.encode()
    async with center.runtime_sessions() as session, session.begin():
        artifact = Artifact(
            id=uuid.uuid4(),
            tenant_id=center.tenant_id,
            run_id=None,
            task_id=task_id,
            agent_id=tree.agent_id,
            user_id=tree.owner_id,
            artifact_type="OUTPUT",
            storage_key=f"tasks/{center.tenant_id}/{task_id}/{uuid.uuid4()}/{filename}",
            media_type="text/plain",
            size=len(content),
            checksum="sha256:" + hashlib.sha256(content + filename.encode()).hexdigest(),
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


async def _seed_outbox(
    center: _Center,
    tree: _TaskTree,
    attachments: list[AttachmentRef],
    *,
    status: str,
    error_code: str | None = None,
) -> None:
    async with center.sessions() as session, session.begin():
        session.add(
            TaskMessageOutbox(
                id=uuid.uuid4(),
                tenant_id=center.tenant_id,
                task_id=tree.execution_id,
                message_key=f"execution:{tree.execution_id}:final",
                kind="TASK_RESULT",
                target_key=TARGET_KEY,
                route_json=ROUTE_JSON,
                payload_json={
                    "text": "周报已生成",
                    "attachments": [item.model_dump(mode="json") for item in attachments],
                },
                status=status,
                attempts=1,
                next_attempt_at=datetime.now(UTC),
                last_error_code=error_code,
            )
        )


async def _seed_message_delivery(
    center: _Center,
    tree: _TaskTree,
    attachments: list[AttachmentRef],
    *,
    status: str,
    fallback_url: str | None = None,
) -> None:
    async with center.runtime_sessions() as session, session.begin():
        session.add(
            MessageDelivery(
                tenant_id=center.tenant_id,
                owner_type="TASK",
                owner_id=tree.execution_id,
                message_key=f"execution:{tree.execution_id}:final",
                kind="TASK_RESULT",
                target_key=TARGET_KEY,
                route_json=ROUTE_JSON,
                payload_json={
                    "text": "周报已生成",
                    "attachments": [item.model_dump(mode="json") for item in attachments],
                },
                status=status,
                attempt_count=1,
                next_attempt_at=datetime.now(UTC),
                fallback_url=fallback_url,
                last_error_code="DEGRADED" if status == "DEGRADED" else None,
            )
        )


async def _detail(center: _Center, tree: _TaskTree) -> dict[str, object]:
    response = await center.client.get(
        f"/internal/executions/{tree.execution_id}",
        headers=_headers(center, tree),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _artifact_deliveries(center: _Center, tree: _TaskTree, name: str) -> list[dict]:
    detail = await _detail(center, tree)
    artifacts = detail["result_artifacts"]
    artifact = next(item for item in artifacts if item["name"] == name)
    return artifact["deliveries"]


async def test_pending_outbox_is_visible_as_pending_not_web_only(
    task_center: _Center,
) -> None:
    tree = await _seed_task(task_center)
    first = await _seed_artifact(task_center, tree, filename="a.txt")
    second = await _seed_artifact(task_center, tree, filename="b.txt")
    await _seed_outbox(task_center, tree, [first, second], status="PENDING")

    deliveries = await _artifact_deliveries(task_center, tree, "a.txt")

    assert deliveries == [
        {
            "target_key": TARGET_KEY,
            "target_label": "企微",
            "status": "PENDING",
            "error_code": None,
            "retryable": True,
            "fallback_url": None,
        }
    ]
    assert BOT_ID not in deliveries[0]["target_label"]
    assert EXTERNAL_USER_ID not in deliveries[0]["target_label"]
    assert len(await _artifact_deliveries(task_center, tree, "b.txt")) == 1


async def test_failed_outbox_is_visible_as_failed_not_web_only(
    task_center: _Center,
) -> None:
    tree = await _seed_task(task_center)
    attachment = await _seed_artifact(task_center, tree, filename="failed.txt")
    await _seed_outbox(
        task_center,
        tree,
        [attachment],
        status="FAILED",
        error_code="SCHEDULE_DELIVERY_UNSUPPORTED",
    )

    deliveries = await _artifact_deliveries(task_center, tree, "failed.txt")

    assert deliveries[0]["status"] == "FAILED"
    assert deliveries[0]["error_code"] == "SCHEDULE_DELIVERY_UNSUPPORTED"
    assert deliveries[0]["retryable"] is False
    assert deliveries[0]["target_label"] == "企微"


async def test_applied_outbox_uses_message_delivery_status_and_fallback(
    task_center: _Center,
) -> None:
    tree = await _seed_task(task_center)
    attachment = await _seed_artifact(task_center, tree, filename="degraded.txt")
    await _seed_outbox(task_center, tree, [attachment], status="APPLIED")
    await _seed_message_delivery(
        task_center,
        tree,
        [attachment],
        status="DEGRADED",
        fallback_url="https://example.test/download/token",
    )

    deliveries = await _artifact_deliveries(task_center, tree, "degraded.txt")

    assert deliveries[0]["status"] == "DEGRADED"
    assert deliveries[0]["fallback_url"] == "https://example.test/download/token"
    assert deliveries[0]["target_label"] == "企微"


async def test_delivery_intent_none_has_empty_deliveries(
    task_center: _Center,
) -> None:
    tree = await _seed_task(task_center, intent="NONE")
    await _seed_artifact(task_center, tree, filename="web-only.txt")

    detail = await _detail(task_center, tree)

    assert detail["result_artifacts"][0]["deliveries"] == []


async def test_parent_outbox_covers_aggregated_child_artifacts(
    task_center: _Center,
) -> None:
    """父任务自己没有产物时，仍按父 outbox 给子产物投影投递状态。"""
    tree = await _seed_task(task_center)
    child_id = await _seed_child(task_center, tree, item_key="customer-c")
    attachment = await _seed_artifact_for_task(
        task_center, tree, task_id=child_id, filename="child.txt"
    )
    await _seed_outbox(task_center, tree, [attachment], status="PENDING")

    deliveries = await _artifact_deliveries(task_center, tree, "child.txt")

    assert [item["status"] for item in deliveries] == ["PENDING"]
