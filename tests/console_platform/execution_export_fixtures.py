"""Shared seeds for execution-export repository tests.

The Run/event/operation/task insert helpers are the same ones used by the Run API
tests; importing them keeps the seed shape identical to production writes.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext
from tests.console_platform.test_runs_api import (  # noqa: F401  (re-exported seeds)
    RunSeed,
    _run,
    _seed_event,
    _seed_task,
)

SNAPSHOT_HASH = "sha256:" + "a" * 64

_INSERT_CHILD_TASK = text(
    """
    INSERT INTO task.task_execution
        (id, tenant_id, source_run_id, parent_id, root_id, agent_id, actor_user_id,
         intent_key, skill_id, skill_artifact_id, trigger_type, execution_mode, task_type,
         status, input_json, execution_snapshot_schema_version, execution_snapshot_json,
         snapshot_hash, idempotency_key, priority, attempt, max_attempts, not_before,
         delivery_mode, delivery_status, delivery_key, delivery_attempts)
    VALUES (:task_id, :tenant_id, :run_id, :parent_id, :root_id, :agent_id, :actor_user_id,
            :intent_key, :skill_id, :artifact_id, 'IMMEDIATE', 'ASYNC', 'SKILL', :status,
            '{}'::jsonb, 1, '{}'::jsonb, :snapshot_hash, :idempotency_key, 100, 1, 1,
            now(), 'NONE', 'NONE', :delivery_key, 0)
    """
)

_INSERT_EVENT_PAYLOAD = text(
    """
    INSERT INTO runtime.canonical_event
        (id, tenant_id, conversation_id, run_id, seq, stream_type, event_type, payload_json)
    VALUES (:event_id, :tenant_id, :conversation_id, :run_id, :seq, :stream_type,
            :event_type, cast(:payload AS jsonb))
    """
)

_INSERT_TASK_EVENT = text(
    """
    INSERT INTO task.task_event
        (id, tenant_id, task_id, seq, event_type, payload_json, trace_id)
    VALUES (:event_id, :tenant_id, :task_id, :seq, :event_type, cast(:payload AS jsonb), :trace_id)
    """
)


async def seed_child_task(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    parent_id: uuid.UUID,
    status: str = "FAILED",
) -> uuid.UUID:
    task_id = uuid.uuid4()
    await session.execute(
        _INSERT_CHILD_TASK,
        {
            "task_id": task_id,
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "parent_id": parent_id,
            "root_id": parent_id,
            "agent_id": seed.agent_id,
            "actor_user_id": seed.actor_user_id,
            "intent_key": f"child-{task_id.hex[:8]}",
            "skill_id": uuid.uuid4(),
            "artifact_id": uuid.uuid4(),
            "status": status,
            "snapshot_hash": SNAPSHOT_HASH,
            "idempotency_key": str(task_id),
            "delivery_key": f"task:{task_id}:final",
        },
    )
    return task_id


async def seed_event_payload(
    session: AsyncSession,
    tenant: TenantContext,
    seed: RunSeed,
    *,
    seq: int,
    event_type: str,
    payload: str,
    stream_type: str | None = None,
) -> None:
    await session.execute(
        _INSERT_EVENT_PAYLOAD,
        {
            "event_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "conversation_id": seed.conversation_id,
            "run_id": seed.run_id,
            "seq": seq,
            "stream_type": stream_type,
            "event_type": event_type,
            "payload": payload,
        },
    )


async def seed_task_event(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    task_id: uuid.UUID,
    seq: int = 1,
    event_type: str = "FAILED",
    payload: str = '{"error_code": "SKILL_EXECUTION_FAILED", "detail": "diagnostic"}',
    trace_id: str | None = None,
) -> None:
    await session.execute(
        _INSERT_TASK_EVENT,
        {
            "event_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "task_id": task_id,
            "seq": seq,
            "event_type": event_type,
            "payload": payload,
            "trace_id": trace_id,
        },
    )


@pytest.fixture(autouse=True)
async def cleanup_execution_export_rows(tenant: TenantContext) -> Any:
    """Delete task/runtime rows created by these tests in FK-safe order.

    The shared tenant fixture only owns control rows; execution-export tests create task
    trees and event rows, so they must remove task_event before task_execution.
    """
    yield
    from muad_console_platform.infrastructure.db import get_session_factory

    statements = (
        "task.task_event",
        "task.task_execution",
        "runtime.tool_control_outbox",
        "runtime.tool_result_inbox",
        "runtime.run_continuation",
        "runtime.tool_operation",
        "runtime.canonical_event",
        "runtime.run_interrupt",
        "runtime.runtime_snapshot",
        "runtime.artifact",
        "runtime.run_record",
        "runtime.conversation",
        "control.platform_user",
        "control.agent_definition",
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        for table in statements:
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id LIKE :tenant_prefix"),
                {"tenant_prefix": f"{tenant.tenant_id}%"},
            )
        await session.commit()


def repo_session() -> Any:
    """Fresh session factory (repository must own the first statement/snapshot)."""
    from muad_console_platform.infrastructure.db import get_session_factory

    return get_session_factory()
