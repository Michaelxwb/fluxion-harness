"""E-01: learning pipeline failures degrade without touching the Run (TASK-004)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from muad_agent_runtime.application import learning_replay as replay_module
from muad_agent_runtime.application.learning_replay import LearningReplay, ReplayLimits
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    EvalCase,
    EvalReport,
    RunRecord,
)
from muad_contracts import RunStatus


async def _seed(tenant: str) -> uuid.UUID:
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id, tenant_id=tenant, user_id=uuid.uuid4(), agent_id=uuid.uuid4()
            )
        )
        await session.flush()
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
        for index in range(2):
            session.add(
                EvalCase(
                    tenant_id=tenant,
                    task_family="skill:demo",
                    source_run_id=run_id,
                    input_ref_json={"run_id": str(run_id), "index": index},
                    criteria_json={"items": [], "incomplete": False, "split": "train"},
                    item_count=1,
                    content_hash=f"hash-{index}-{uuid.uuid4()}",
                    version=index + 1,
                )
            )
        await session.commit()
    return run_id


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def test_e01_learning_failure_degrades_to_no_learning(
    database_guard: None, monkeypatch, caplog
) -> None:
    tenant = f"degrade-{uuid.uuid4()}"
    run_id = await _seed(tenant)
    recorded: list[tuple[str, dict[str, str]]] = []

    def _record(name: str, amount: float = 1.0, labels=None) -> None:
        recorded.append((name, dict(labels or {})))

    monkeypatch.setattr(replay_module, "record_counter", _record)

    async def failing(case: EvalCase, experience_key: str | None, experience_user_id=None):
        raise RuntimeError("model unavailable")

    replay = LearningReplay(
        get_session_factory(), failing, ReplayLimits(min_cases=2, max_cases=50)
    )
    try:
        with caplog.at_level("WARNING"):
            decision = await replay.run_safely(
                tenant, "skill:demo", experience_key="proc-e", base_config_hash="base-e"
            )
        assert decision is None
        assert any(name == "learning_pipeline_failures_total" for name, _ in recorded)
        assert any("learning_replay_failed" in record.message for record in caplog.records)

        async with get_session_factory()() as session:
            status = await session.scalar(
                sa.select(RunRecord.status).where(RunRecord.id == run_id)
            )
            reports = await session.scalar(
                sa.select(sa.func.count()).select_from(EvalReport).where(
                    EvalReport.tenant_id == tenant
                )
            )
        assert status == RunStatus.COMPLETED
        assert reports == 0
    finally:
        await _cleanup(tenant)
