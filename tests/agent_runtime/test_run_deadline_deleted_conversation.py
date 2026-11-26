"""到期扫描必须能收尾「会话在执行期间被删除」的 Run（真实 PG，不 mock 写入路径）。

实机症状：webchat 会话被删除后，会话里那条 RUNNING 的 Run 到期时，`EventWriter._next_seq`
因 `conversation.is_deleted` 抛 `COMMON_NOT_FOUND`，整个扫描批次被第一条坏行打断，
runtime 每个 tick 刷一条 `run_deadline_sweep_failed`。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application import run_deadline
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from muad_api import AppError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.async_tool_submissions import seed_submission

pytestmark = pytest.mark.integration


@pytest.fixture
def runtime_factory(async_tool_database) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_tool_database, expire_on_commit=False)


async def _expire_deadline(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run_id)
        assert row is not None
        row.deadline_at = sa.func.now() - timedelta(seconds=1)
        row.status = "RUNNING"


async def _delete_conversation(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
    async with factory() as session, session.begin():
        run = await session.get(RunRecord, run_id)
        assert run is not None
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        conversation.is_deleted = True


async def _append(factory, run, *, allow_deleted: bool) -> int:
    async with factory() as session, session.begin():
        return await EventWriter(session, allow_deleted_conversation=allow_deleted).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_FAILED",
            stream_type="run.failed",
            payload={"status": "FAILED", "error_code": "PROBE"},
        )


async def test_deadline_sweep_closes_run_whose_conversation_was_deleted(runtime_factory):
    _context, _skill, run = await seed_submission(runtime_factory)
    await _expire_deadline(runtime_factory, run.id)
    await _delete_conversation(runtime_factory, run.id)

    assert await run_deadline.expire_runs(runtime_factory) == 1

    async with runtime_factory() as session:
        row = await session.get(RunRecord, run.id)
        assert row is not None
        assert row.status == "FAILED" and row.error_code == "RUN_DEADLINE_EXCEEDED"
        events = list(
            await session.scalars(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.run_id == run.id, CanonicalEvent.event_type == "RUN_FAILED"
                )
            )
        )
        assert len(events) == 1
        reply = await session.scalar(sa.select(CanonicalEvent).where(
            CanonicalEvent.run_id == run.id, CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
        ))
        assert reply.payload_json["text"] == events[0].payload_json["final_text"]
        assert any("RUN_DEADLINE_EXCEEDED" in line for line in reply.payload_json["technical_details"])


async def test_plain_appends_still_reject_deleted_conversations(runtime_factory):
    """开关只给收尾类写入方：普通追加仍然拒绝已删除会话。"""

    _context, _skill, run = await seed_submission(runtime_factory)
    await _delete_conversation(runtime_factory, run.id)

    with pytest.raises(AppError) as caught:
        await _append(runtime_factory, run, allow_deleted=False)
    assert caught.value.code == "COMMON_NOT_FOUND"
    assert await _append(runtime_factory, run, allow_deleted=True) > 0


async def test_deadline_sweep_isolates_a_failing_candidate(runtime_factory, monkeypatch):
    """一条坏行只跳过自己：后面的到期 Run 仍要被收尾，异常不外溢到扫描循环。"""

    _context, _skill, first = await seed_submission(runtime_factory)
    _context, _skill, second = await seed_submission(runtime_factory)
    await _expire_deadline(runtime_factory, first.id)
    await _expire_deadline(runtime_factory, second.id)
    async with runtime_factory() as session, session.begin():
        row = await session.get(RunRecord, first.id)
        assert row is not None
        row.deadline_at = sa.func.now() - timedelta(seconds=60)

    original = run_deadline._expire_one

    async def flaky(factory, candidate, now):  # type: ignore[no-untyped-def]
        if candidate.id == first.id:
            raise AppError("COMMON_INTERNAL_ERROR", "probe")
        return await original(factory, candidate, now)

    monkeypatch.setattr(run_deadline, "_expire_one", flaky)
    assert await run_deadline.expire_runs(runtime_factory) == 1
    async with runtime_factory() as session:
        row = await session.get(RunRecord, second.id)
        assert row is not None and row.status == "FAILED"
