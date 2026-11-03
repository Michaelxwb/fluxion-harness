"""S-03 / E-02: learning scheduler cycles (TASK-002)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from muad_agent_runtime.application.learning_scheduler import LearningSupervisor
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalCase,
    MemoryCommandReceipt,
    MemoryScope,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)
from muad_contracts import RunStatus


class _Settings:
    """只对指定租户开启学习：避免调度器处理共享库里的其他测试租户。"""

    def __init__(self, tenant_id: str, enabled: bool = True) -> None:
        self._tenant, self._enabled = tenant_id, enabled

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        enabled = self._enabled and tenant_id == self._tenant
        return PlatformSettingsSnapshot(
            revision=1,
            settings={"learning": {"learning_enabled": enabled}},
        )


def _events(count: int) -> list[dict[str, Any]]:
    calls = [
        {"id": f"c{index}", "name": "execute_skill", "arguments": {"skill_key": "demo"}}
        for index in range(count)
    ]
    return [
        {"event_type": "USER_MESSAGE", "payload": {"text": "处理客户"}},
        {"event_type": "ASSISTANT_TURN", "payload": {"tool_calls": calls}},
        *[
            {
                "event_type": "TOOL_CALL",
                "payload": {
                    "tool_call_id": call["id"],
                    "tool_name": "execute_skill",
                    "status": "OK",
                },
            }
            for call in calls
        ],
    ]


async def _seed(tenant: str, user: uuid.UUID) -> None:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(Conversation(id=conversation_id, tenant_id=tenant, user_id=user, agent_id=uuid.uuid4()))
        await session.flush()
        seq = 0
        for _ in range(3):
            run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    user_id=user,
                    agent_id=uuid.uuid4(),
                    status=RunStatus.COMPLETED,
                    input_text="处理客户",
                    trace_id=f"trace-{run_id}",
                    end_time=datetime.now(UTC),
                    task_family="skill:demo",
                )
            )
            await session.flush()
            for event in _events(2):
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
        await session.commit()


async def _counts(tenant: str) -> tuple[int, int]:
    async with get_session_factory()() as session:
        cases = await session.scalar(
            sa.select(sa.func.count()).select_from(EvalCase).where(EvalCase.tenant_id == tenant)
        )
        experiences = await session.scalar(
            sa.select(sa.func.count())
            .select_from(UserMemory)
            .where(UserMemory.tenant_id == tenant, UserMemory.category == "PROCEDURE")
        )
    return int(cases or 0), int(experiences or 0)


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(UserMemoryHistory).where(UserMemoryHistory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryCommandReceipt).where(MemoryCommandReceipt.tenant_id == tenant))
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def test_s03_scheduler_extracts_and_generates_idempotently(database_guard: None) -> None:
    tenant, user = f"sched-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user)
    supervisor = LearningSupervisor(get_session_factory(), settings_client=_Settings(tenant))
    try:
        first = await supervisor.run_once()
        assert first.enabled >= 1
        assert first.extracted >= 1
        assert first.candidates >= 1
        cases, experiences = await _counts(tenant)
        assert cases == 3
        assert experiences == 1

        second = await supervisor.run_once()
        assert second.candidates == 0
        assert await _counts(tenant) == (3, 1)
    finally:
        await _cleanup(tenant)


async def test_s03_scheduler_disabled_tenant_is_skipped(database_guard: None) -> None:
    tenant, user = f"sched-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user)
    supervisor = LearningSupervisor(get_session_factory(), settings_client=_Settings(tenant, enabled=False))
    try:
        report = await supervisor.run_once()
        assert report.skipped >= 1
        assert report.extracted == 0 and report.candidates == 0
        assert await _counts(tenant) == (0, 0)
    finally:
        await _cleanup(tenant)
