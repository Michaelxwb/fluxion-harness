"""S-10 / S-15: two-arm replay gate with cached baseline and per-item judging (TASK-004)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from muad_agent_runtime.application.learning_replay import (
    PASSED,
    REJECTED,
    CaseOutcome,
    LearningReplay,
    ReplayLimits,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    EvalCase,
    EvalReport,
    RunRecord,
)
from muad_contracts import RunStatus


async def _seed_cases(
    tenant: str, family: str, item_counts: list[int]
) -> list[uuid.UUID]:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
            )
        )
        await session.flush()
        case_ids: list[uuid.UUID] = []
        for index, item_count in enumerate(item_counts):
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
                    task_family=family,
                )
            )
            await session.flush()
            case = EvalCase(
                tenant_id=tenant,
                task_family=family,
                source_run_id=run_id,
                input_ref_json={"run_id": str(run_id)},
                criteria_json={"items": [], "incomplete": False, "split": "train"},
                item_count=item_count,
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


class _CountingRunner:
    def __init__(self, candidate: CaseOutcome, baseline: CaseOutcome | None = None) -> None:
        self.candidate = candidate
        self.baseline = baseline or CaseOutcome(False, "baseline fails", 2, 1)
        self.calls: list[tuple[str | None, uuid.UUID]] = []

    async def __call__(self, case: EvalCase, experience_key: str | None) -> CaseOutcome:
        self.calls.append((experience_key, case.id))
        return self.baseline if experience_key is None else self.candidate

    def arm_calls(self, experience_key: str | None) -> int:
        return sum(1 for key, _ in self.calls if key == experience_key)


async def _reports(tenant: str) -> list[EvalReport]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.scalars(
                    sa.select(EvalReport)
                    .where(EvalReport.tenant_id == tenant)
                    .order_by(EvalReport.create_time, EvalReport.version)
                )
            ).all()
        )


def _limits() -> ReplayLimits:
    return ReplayLimits(min_cases=2, max_cases=50, min_improvement=0.05)


async def test_s10_gate_requires_improvement_and_reuses_baseline(database_guard: None) -> None:
    tenant = f"replay-{uuid.uuid4()}"
    await _seed_cases(tenant, "skill:demo", [2, 2])
    runner = _CountingRunner(CaseOutcome(True, "", 2, 2))
    replay = LearningReplay(get_session_factory(), runner, _limits())
    try:
        first = await replay.run(
            tenant, "skill:demo", experience_key="proc-1", base_config_hash="base-1"
        )
        assert first.status == PASSED
        assert first.improvement == 1.0
        assert runner.arm_calls(None) == 2
        assert runner.arm_calls("proc-1") == 2

        second = await replay.run(
            tenant, "skill:demo", experience_key="proc-1", base_config_hash="base-1"
        )
        assert second.status == PASSED
        # 基线臂按 (case 集合, base_config_hash) 复用，不重跑。
        assert runner.arm_calls(None) == 2

        rows = await _reports(tenant)
        baseline_rows = [row for row in rows if row.experience_key is None]
        candidate_rows = [row for row in rows if row.experience_key == "proc-1"]
        assert len(baseline_rows) == 1
        assert len(candidate_rows) == 2
        assert [row.version for row in candidate_rows] == [1, 2]
        assert candidate_rows[-1].decision_json["status"] == PASSED
        assert candidate_rows[-1].metrics_json["baseline"]["success_rate"] == 0.0
    finally:
        await _cleanup(tenant)


async def test_s10_gate_rejects_without_improvement(database_guard: None) -> None:
    tenant = f"replay-{uuid.uuid4()}"
    await _seed_cases(tenant, "skill:demo", [1, 1])
    runner = _CountingRunner(
        CaseOutcome(False, "no better", 1, 1), baseline=CaseOutcome(False, "baseline", 1, 1)
    )
    replay = LearningReplay(get_session_factory(), runner, _limits())
    try:
        decision = await replay.run(
            tenant, "skill:demo", experience_key="proc-2", base_config_hash="base-2"
        )
        assert decision.status == REJECTED
        assert decision.improvement == 0.0
    finally:
        await _cleanup(tenant)


async def test_s15_batch_items_missing_or_failed_make_the_case_fail(database_guard: None) -> None:
    tenant = f"replay-{uuid.uuid4()}"
    await _seed_cases(tenant, "skill:demo", [3, 3])
    partial = _CountingRunner(
        CaseOutcome(True, "one item missing", items_total=3, items_passed=2)
    )
    replay = LearningReplay(get_session_factory(), partial, _limits())
    try:
        decision = await replay.run(
            tenant, "skill:demo", experience_key="proc-3", base_config_hash="base-3"
        )
        assert decision.candidate.passed == 0
        assert decision.candidate.items_total == 6
        assert decision.candidate.items_passed == 4
        assert decision.status == REJECTED
        rows = await _reports(tenant)
        candidate = [row for row in rows if row.experience_key == "proc-3"][-1]
        assert candidate.metrics_json["items_total"] == 6
        assert candidate.decision_json["status"] == REJECTED
    finally:
        await _cleanup(tenant)

    tenant = f"replay-{uuid.uuid4()}"
    await _seed_cases(tenant, "skill:demo", [3, 3])
    complete = _CountingRunner(
        CaseOutcome(True, "", items_total=3, items_passed=3),
        baseline=CaseOutcome(False, "baseline", 3, 0),
    )
    replay = LearningReplay(get_session_factory(), complete, _limits())
    try:
        decision = await replay.run(
            tenant, "skill:demo", experience_key="proc-4", base_config_hash="base-4"
        )
        assert decision.candidate.passed == 2
        assert decision.status == PASSED
    finally:
        await _cleanup(tenant)
