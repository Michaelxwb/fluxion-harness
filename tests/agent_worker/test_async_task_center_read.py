"""TASK-004: background list filters and shared execution detail projections."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from muad_agent_runtime.infrastructure.models.runtime import Artifact
from muad_agent_worker.api.tasks import list_payloads
from muad_agent_worker.application.schedule_read import ScheduleReadService
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_contracts import TaskStatus, TaskType
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.integration


async def _seed_background_tree(factory, tenant: str):
    parent, _, _ = await seed_operation(factory, tenant=tenant)
    child_a, _, _ = await seed_operation(factory, tenant=tenant)
    child_b, _, _ = await seed_operation(factory, tenant=tenant)
    async with factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == parent.id)
            .values(
                status=TaskStatus.COMPLETED.value,
                task_type=TaskType.BATCH.value,
                result_json={
                    "mode": "BEST_EFFORT",
                    "total": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "cancelled": 0,
                },
                execution_snapshot_json={"skills": [{"name": "客户对账"}]},
                execution_snapshot_schema_version=3,
                finished_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == child_a.id)
            .values(
                parent_id=parent.id,
                item_key="customer-a",
                input_json={"customer": "客户 A"},
                status=TaskStatus.COMPLETED.value,
                finished_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == child_b.id)
            .values(
                parent_id=parent.id,
                item_key="customer-b",
                input_json={"customer": "客户 B"},
                status=TaskStatus.FAILED.value,
                error_code="HTTP_401",
                error_message="HTTP 401 Unauthorized\ncheck token",
                finished_at=datetime.now(UTC),
            )
        )
        session.add(
            Artifact(
                tenant_id=tenant,
                task_id=child_a.id,
                conversation_id=None,
                agent_id=child_a.agent_id,
                user_id=child_a.actor_user_id,
                artifact_type="TASK_OUTPUT",
                storage_key=f"tasks/{tenant}/{child_a.id}/result.txt",
                media_type="text/plain",
                size=3,
                checksum="sha256:" + "a" * 64,
                metadata_json={"filename": "result.txt", "version": 1, "versions": []},
            )
        )
    async with factory() as session:
        stored_parent = await session.get(TaskExecution, parent.id)
        stored_child_a = await session.get(TaskExecution, child_a.id)
        stored_child_b = await session.get(TaskExecution, child_b.id)
    assert stored_parent and stored_child_a and stored_child_b
    return stored_parent, stored_child_a, stored_child_b


async def test_background_list_filters_children_and_projects_counts(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    tenant = f"test-{uuid4()}"
    parent, child_a, child_b = await _seed_background_tree(factory, tenant)
    async with factory() as session:
        items, total = await TaskService(session).list(
            tenant, background_only=True, agent_ids=(parent.agent_id,)
        )
        rows = await list_payloads(TaskService(session), tenant, items)

    ids = {row["task_id"] for row in rows}
    assert str(parent.id) in ids
    assert str(child_a.id) not in ids
    assert str(child_b.id) not in ids
    assert total == 1
    row = rows[0]
    assert row["skill_name"] == "客户对账"
    assert row["counts"]["failed"] == 1
    assert row["partial_failure"] is True
    assert row["has_artifacts"] is True


async def test_execution_detail_aggregates_children_and_artifacts(async_tool_database) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    tenant = f"test-{uuid4()}"
    parent, child_a, child_b = await _seed_background_tree(factory, tenant)
    async with factory() as session:
        detail = await ScheduleReadService(session).execution_detail(execution=parent)

    assert detail.agent_id == parent.agent_id
    assert detail.task_type is TaskType.BATCH
    assert detail.skill_name == "客户对账"
    assert detail.counts.failed == 1
    assert detail.partial_failure is True
    assert detail.artifact_total == 1
    assert [item.artifact_id for item in detail.result_artifacts]
    assert detail.children.total == 2
    labels = {item.label for item in detail.children.items}
    assert labels == {"客户 A", "客户 B"}
    failed = next(item for item in detail.children.items if item.status is TaskStatus.FAILED)
    assert failed.error_message_summary == "HTTP 401 Unauthorized"
    assert failed.artifact_count == 0
    success = next(item for item in detail.children.items if item.status is TaskStatus.COMPLETED)
    assert success.artifact_count == 1
