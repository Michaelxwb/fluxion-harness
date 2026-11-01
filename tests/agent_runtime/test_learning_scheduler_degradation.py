"""E-01: learning scheduler failures degrade without touching the Run (TASK-005)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application import learning_scheduler as scheduler_module
from muad_agent_runtime.application.learning_scheduler import LearningSupervisor
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import EvalCase, RunRecord, UserMemory

from tests.agent_runtime.learning_wiring_support import Settings, cleanup, seed_runs


async def test_e01_scheduler_failure_degrades_to_no_learning(
    database_guard: None, monkeypatch, caplog
) -> None:
    tenant, user = f"degrade-{uuid.uuid4()}", uuid.uuid4()
    await seed_runs(tenant, user, 3)
    recorded: list[tuple[str, dict[str, str]]] = []

    def _record(name: str, amount: float = 1.0, labels=None) -> None:
        recorded.append((name, dict(labels or {})))

    monkeypatch.setattr(scheduler_module, "record_counter", _record)

    class _BrokenExtractor:
        async def extract(self, *args, **kwargs):
            raise RuntimeError("extractor down")

    supervisor = LearningSupervisor(
        get_session_factory(),
        settings_client=Settings(tenant),
        extractor=_BrokenExtractor(),
    )
    try:
        with caplog.at_level("WARNING"):
            report = await supervisor.run_once()
        assert report.enabled >= 1 and report.extracted == 0 and report.candidates == 0
        assert any(name == "learning_pipeline_failures_total" for name, _ in recorded)
        assert any("learning_scheduler_tenant_failed" in record.message for record in caplog.records)

        async with get_session_factory()() as session:
            cases = await session.scalar(
                sa.select(sa.func.count()).select_from(EvalCase).where(EvalCase.tenant_id == tenant)
            )
            experiences = await session.scalar(
                sa.select(sa.func.count()).select_from(UserMemory).where(UserMemory.tenant_id == tenant)
            )
            statuses = (
                await session.scalars(
                    sa.select(RunRecord.status).where(RunRecord.tenant_id == tenant)
                )
            ).all()
        assert int(cases or 0) == 0 and int(experiences or 0) == 0
        assert statuses == ["COMPLETED", "COMPLETED", "COMPLETED"]
    finally:
        await cleanup(tenant)
