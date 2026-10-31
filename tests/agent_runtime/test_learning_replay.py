"""S-03: replay reports are reproducible and immutable (TASK-004)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application.learning_replay import CaseOutcome, LearningReplay, ReplayLimits
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    EvalCase,
    EvalReport,
    RunRecord,
)
from muad_contracts import RunStatus
from datetime import UTC, datetime


async def _seed(tenant: str) -> list[uuid.UUID]:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id, tenant_id=tenant, user_id=uuid.uuid4(), agent_id=uuid.uuid4()
            )
        )
        await session.flush()
        case_ids: list[uuid.UUID] = []
        for index in range(2):
            run_id = uuid.uuid4()
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    user_id=uuid.uuid4(),
                    agent_id=uuid.uuid4(),
                    status=RunStatus.COMPLETED,
                    input_text="seed",
                    trace_id=f"trace-{run_id}",
                    end_time=datetime.now(UTC),
                    task_family="skill:demo",
                )
            )
            await session.flush()
            case = EvalCase(
                tenant_id=tenant,
                task_family="skill:demo",
                source_run_id=run_id,
                input_ref_json={"run_id": str(run_id)},
                criteria_json={"items": [], "incomplete": False, "split": "train"},
                item_count=1,
                content_hash=f"hash-{index}-{uuid.uuid4()}",
                version=1,
            )
            session.add(case)
            await session.flush()
            case_ids.append(case.id)
        await session.commit()
    return case_ids


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def test_s03_replay_reports_are_reproducible_and_immutable(database_guard: None) -> None:
    tenant = f"replay-{uuid.uuid4()}"
    case_ids = await _seed(tenant)

    async def runner(case: EvalCase, experience_key: str | None) -> CaseOutcome:
        return CaseOutcome(True, "", 1, 1)

    replay = LearningReplay(
        get_session_factory(), runner, ReplayLimits(min_cases=2, max_cases=50)
    )
    try:
        first = await replay.run(
            tenant, "skill:demo", experience_key="proc-r", base_config_hash="base-r",
            refresh_baseline=True,
        )
        second = await replay.run(
            tenant, "skill:demo", experience_key="proc-r", base_config_hash="base-r",
            refresh_baseline=True,
        )
        assert first.candidate == second.candidate
        async with get_session_factory()() as session:
            rows = list(
                (
                    await session.scalars(
                        sa.select(EvalReport)
                        .where(
                            EvalReport.tenant_id == tenant,
                            EvalReport.experience_key == "proc-r",
                        )
                        .order_by(EvalReport.version)
                    )
                ).all()
            )
        assert [row.version for row in rows] == [1, 2]
        assert rows[0].content_hash == rows[1].content_hash
        assert rows[0].metrics_json == rows[1].metrics_json
        assert {item["case_id"] for item in rows[0].report_json["cases"]} == {
            str(case_id) for case_id in case_ids
        }
    finally:
        await _cleanup(tenant)
