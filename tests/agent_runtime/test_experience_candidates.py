"""S-05 / S-16 / E-02: cross-Run experience candidates (TASK-006)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_runtime.application.experience_candidates import (
    CandidateLimits,
    ExperienceCandidates,
    experience_key,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    MemoryCommandReceipt,
    MemoryScope,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)
from muad_agent_core.memory.rank import MemoryRecord, select_for_injection
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory
from muad_contracts import RunStatus


def _skill_events(count: int) -> list[dict[str, Any]]:
    calls = [
        {"id": f"call-{index}", "name": "execute_skill", "arguments": {"skill_key": "demo"}}
        for index in range(count)
    ]
    events: list[dict[str, Any]] = [{"event_type": "ASSISTANT_TURN", "payload": {"tool_calls": calls}}]
    events.extend(
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": call["id"], "tool_name": "execute_skill", "status": "OK"},
        }
        for call in calls
    )
    return events


async def _seed_runs(
    tenant: str,
    user: uuid.UUID,
    *,
    success: int,
    items: int,
    failed: int = 0,
    signal: bool = False,
) -> list[uuid.UUID]:
    conversation_id = uuid.uuid4()
    run_ids: list[uuid.UUID] = []
    async with get_session_factory()() as session:
        session.add(
            Conversation(id=conversation_id, tenant_id=tenant, user_id=user, agent_id=uuid.uuid4())
        )
        await session.flush()
        total = success + failed
        seq = 0
        for index in range(total):
            run_id = uuid.uuid4()
            run_ids.append(run_id)
            status = RunStatus.COMPLETED if index < success else RunStatus.FAILED
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    user_id=user,
                    agent_id=uuid.uuid4(),
                    status=status,
                    input_text="seed",
                    trace_id=f"trace-{run_id}",
                    end_time=datetime.now(UTC),
                    task_family="skill:demo",
                )
            )
            await session.flush()
            events = (
                [{"event_type": "USER_MESSAGE", "payload": {"text": "处理客户"}}]
                + _skill_events(items)
                if index < success
                else []
            )
            for event in events:
                seq += 1
                session.add(
                    CanonicalEvent(
                        tenant_id=tenant,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        seq=seq,
                        event_type=event["event_type"],
                        payload_json=event["payload"],
                    )
                )
            if index == success and signal:
                session.add(
                    CanonicalEvent(
                        tenant_id=tenant,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        seq=seq + 1,
                        event_type="LEARNING_SIGNAL",
                        payload_json={"source_run_id": str(run_id), "signal_type": "NEGATION"},
                    )
                )
        await session.commit()
    return run_ids


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.delete(UserMemoryHistory).where(UserMemoryHistory.tenant_id == tenant)
        )
        await session.execute(
            sa.delete(MemoryCommandReceipt).where(MemoryCommandReceipt.tenant_id == tenant)
        )
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def _experience(tenant: str, user: uuid.UUID) -> UserMemory | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant,
                UserMemory.user_id == user,
                UserMemory.memory_key == experience_key("skill:demo"),
            )
        )


def _limits(min_runs: int = 3) -> CandidateLimits:
    return CandidateLimits(min_success_runs=min_runs)


async def test_s05_successful_traces_produce_proposed_experiences(database_guard: None) -> None:
    tenant, user = f"exp-{uuid.uuid4()}", uuid.uuid4()
    run_ids = await _seed_runs(tenant, user, success=3, items=2, failed=1, signal=True)
    try:
        report = await ExperienceCandidates(limits=_limits()).generate(tenant)
        assert report.created == 1
        assert report.families == ("skill:demo",)

        row = await _experience(tenant, user)
        assert row is not None
        assert row.category == MemoryCategory.PROCEDURE
        assert row.activation_state == "PROPOSED"
        assert row.author == "AGENT"
        assert row.task_signature == "skill:demo"
        assert "批量" in (row.trigger_text or "")
        evidence = row.evidence_json or {}
        assert evidence["success_runs"] == 3
        assert evidence["item_total"] == 6
        assert set(evidence["source_run_ids"]) == {str(run_id) for run_id in run_ids[:3]}

        record = MemoryRecord(
            memory_key=row.memory_key,
            content=FactContent(value=row.content_json["value"]),
            author=MemoryAuthor.AGENT,
            category=MemoryCategory.PROCEDURE,
            version=row.version,
            updated_at=datetime.now(UTC),
        )
        selected = select_for_injection(
            [record], query="客户报表", now=datetime.now(UTC), inject_inferred=False
        )
        assert selected == ()

        again = await ExperienceCandidates(limits=_limits()).generate(tenant)
        assert again.created == 0
    finally:
        await _cleanup(tenant)


async def test_s16_batch_traces_produce_procedure_with_batch_trigger(database_guard: None) -> None:
    tenant, user = f"exp-{uuid.uuid4()}", uuid.uuid4()
    await _seed_runs(tenant, user, success=3, items=10)
    try:
        report = await ExperienceCandidates(limits=_limits()).generate(tenant)
        assert report.created == 1
        row = await _experience(tenant, user)
        assert row is not None
        assert "批量目标" in (row.trigger_text or "")
        assert "逐个目标" in row.content_json["value"]
        assert (row.evidence_json or {})["item_total"] == 30
    finally:
        await _cleanup(tenant)


async def test_e02_forget_epoch_blocks_resurrection(database_guard: None) -> None:
    tenant, user = f"exp-{uuid.uuid4()}", uuid.uuid4()
    await _seed_runs(tenant, user, success=3, items=1)
    key = experience_key("skill:demo")
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1, forget_epoch=1))
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key=key,
                category="PROCEDURE",
                content_json={"value": "用户删除过的经验"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=30),
                is_deleted=True,
                enabled=False,
            )
        )
        await session.commit()
    try:
        report = await ExperienceCandidates(limits=_limits()).generate(tenant)
        assert report.created == 0
        assert report.skipped >= 1

        async with get_session_factory()() as session:
            rows = list(
                (
                    await session.scalars(
                        sa.select(UserMemory).where(
                            UserMemory.tenant_id == tenant,
                            UserMemory.user_id == user,
                            UserMemory.memory_key == key,
                        )
                    )
                ).all()
            )
            scope = await session.scalar(
                sa.select(MemoryScope).where(
                    MemoryScope.tenant_id == tenant, MemoryScope.user_id == user
                )
            )
        assert len(rows) == 1
        assert rows[0].is_deleted is True
        assert rows[0].content_json["value"] == "用户删除过的经验"
        assert scope is not None and scope.forget_epoch == 1
    finally:
        await _cleanup(tenant)
