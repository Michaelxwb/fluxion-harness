"""[S-13] Runtime 队列/等待年龄/内存 backlog 与有界准入（TASK-005）。

真实边界：真实 PostgreSQL（隔离库 + alembic head）里的 `runtime.run_record` /
`memory_consolidation_job` 行；不 mock 计数与时间（排队年龄由 PG `now()` 计算）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_runtime.application.capacity_metrics import (
    publish_capacity_gauges,
    read_capacity_gauges,
)
from muad_agent_runtime.application.run_service import (
    AdmissionQueueFull,
    BoundedAdmissionGate,
)
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    MemoryConsolidationJob,
    RunRecord,
    RunStatus,
)
from muad_api import render_metrics

pytestmark = pytest.mark.integration

TENANT = "e2e-runtime-capacity"
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
async def capacity_db(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, str]]:
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    await runtime_db.dispose_engine()
    try:
        yield async_tool_datastore
    finally:
        await runtime_db.dispose_engine()


def _run(
    *,
    status: RunStatus,
    queue_stage: str | None,
    queued_at: datetime | None,
) -> RunRecord:
    return RunRecord(
        tenant_id=TENANT,
        conversation_id=uuid.uuid4(),
        user_id=USER,
        agent_id=AGENT,
        status=status,
        input_text="capacity",
        trace_id=uuid.uuid4().hex,
        queue_stage=queue_stage,
        queued_at=queued_at,
        queue_deadline_at=(queued_at + timedelta(seconds=300)) if queued_at else None,
    )


async def _seed_capacity_rows() -> None:
    async with runtime_db.get_session_factory()() as session:
        now = datetime.now(UTC)
        conversations: list[Conversation] = []
        runs: list[RunRecord] = []
        for stage, status in (("WAITING_RUNTIME", RunStatus.CREATED), ("WAITING_RUNTIME", RunStatus.CREATED)):
            conversation = Conversation(tenant_id=TENANT, user_id=USER, agent_id=AGENT)
            conversations.append(conversation)
            runs.append(_run(status=status, queue_stage=stage, queued_at=now - timedelta(seconds=10)))
        conversation = Conversation(tenant_id=TENANT, user_id=USER, agent_id=AGENT)
        conversations.append(conversation)
        runs.append(_run(status=RunStatus.RUNNING, queue_stage=None, queued_at=None))
        session.add_all(conversations)
        await session.flush()
        for run, conversation in zip(runs, conversations, strict=True):
            run.conversation_id = conversation.id
        session.add_all(runs)
        await session.flush()
        # memory backlog：PENDING 2 / RUNNING 1 / RETRY 1
        for index, status in enumerate(("PENDING", "PENDING", "RUNNING", "RETRY")):
            session.add(
                MemoryConsolidationJob(
                    tenant_id=TENANT,
                    run_id=uuid.uuid4(),
                    snapshot_id=uuid.uuid4(),
                    conversation_id=conversations[0].id,
                    user_id=USER,
                    through_seq=index + 1,
                    status=status,
                    enqueued_scope_revision=0,
                )
            )
        await session.commit()


async def _cleanup_capacity_rows() -> None:
    from sqlalchemy import delete

    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            delete(MemoryConsolidationJob).where(MemoryConsolidationJob.tenant_id == TENANT)
        )
        await session.execute(delete(RunRecord).where(RunRecord.tenant_id == TENANT))
        await session.execute(delete(Conversation).where(Conversation.tenant_id == TENANT))
        await session.commit()


async def test_s13_capacity_gauges_read_real_queue_and_memory_rows(
    capacity_db: dict[str, str],
) -> None:
    await _cleanup_capacity_rows()
    await _seed_capacity_rows()
    try:
        gauges = await read_capacity_gauges(
            runtime_db.get_session_factory(), tenant_id=TENANT
        )
        assert gauges.queue_depth["WAITING_RUNTIME"] == 2
        assert gauges.queue_depth["WAITING_MODEL"] == 0
        assert gauges.oldest_queue_age_sec["WAITING_RUNTIME"] >= 9.0
        assert gauges.execution_inflight == 1
        assert gauges.open_runs == 3
        assert gauges.memory_jobs == {"PENDING": 2, "RUNNING": 1, "RETRY": 1}

        publish_capacity_gauges(gauges)
        rendered = render_metrics()
        for name in (
            "runtime_queue_depth",
            "runtime_oldest_queue_age_seconds",
            "runtime_execution_inflight",
            "runtime_open_runs",
            "runtime_memory_job_queue",
            "runtime_admission_inflight",
            "runtime_admission_waiting",
        ):
            assert name in rendered, f"{name} 未发布到 /metrics"
    finally:
        await _cleanup_capacity_rows()


async def test_s13_admission_gate_rejects_when_queue_is_full() -> None:
    gate = BoundedAdmissionGate(limit=1, queue_limit=0)
    async with gate:
        with pytest.raises(AdmissionQueueFull):
            async with gate:
                pass
        assert gate.stats.inflight == 1
        assert gate.stats.waiting == 0


async def test_s13_admission_gate_bounds_waiters() -> None:
    gate = BoundedAdmissionGate(limit=1, queue_limit=1)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def holder() -> None:
        async with gate:
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    waiter_started = asyncio.Event()

    async def waiter() -> None:
        async with gate:
            waiter_started.set()

    queued = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    assert gate.stats.waiting == 1, gate.stats
    with pytest.raises(AdmissionQueueFull):
        async with gate:
            pass
    release.set()
    await task
    await queued
    assert waiter_started.is_set()


async def test_s13_memory_enqueue_is_bounded_by_queue_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory backlog 到上限后不再入队：不阻塞已完成 Run，也不让 job 无限增长。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from muad_agent_runtime.application.memory_job_enqueue import _insert

    monkeypatch.setenv("MEMORY_CONSOLIDATION_QUEUE_MAX", "2")
    session = AsyncMock()
    session.scalar.return_value = 2
    run = SimpleNamespace(tenant_id="t-cap")
    assert await _insert(session, run, 1) is False
    # 只做了队列计数查询；没有继续读 snapshot/插入 job。
    assert session.scalar.await_count == 1
