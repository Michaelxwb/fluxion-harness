"""TASK-018：定时预算只来自 PlatformSettings.agent，并冻结进 Snapshot。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from muad_agent_worker.application.agentic.run import agent_policy
from muad_agent_worker.application.schedule_admission import (
    AdmissionRequest,
    build_snapshot,
)
from muad_contracts import ScheduleMode, SnapshotBudget
from muad_contracts.platform_settings import default_platform_settings

from agent_worker.test_scheduled_v2_snapshot import make_materials


def _settings(**agent: int):
    base = default_platform_settings()
    return replace(base, agent=replace(base.agent, **agent))


def _request() -> AdmissionRequest:
    return AdmissionRequest(
        tenant_id="tenant-budget",
        agent_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        mode=ScheduleMode.AGENTIC,
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
        # 旧 Revision/payload budget 不得参与。
        budget={"max_steps": 999, "max_tool_calls": 999, "max_tokens": 999},
    )


def test_snapshot_budget_freezes_platform_agent_settings() -> None:
    settings = _settings(
        max_turns=7,
        max_tool_calls=11,
        deadline_ms=120_000,
        max_model_retries=4,
        max_identical_failures=5,
    )
    outcome = build_snapshot(make_materials(mode=ScheduleMode.AGENTIC), _request(), settings)

    assert outcome.snapshot is not None
    budget = outcome.snapshot["budget"]
    assert budget["max_steps"] == 7
    assert budget["max_tool_calls"] == 11
    assert budget["max_tokens"] == 0
    assert budget["deadline_ms"] == 120_000
    assert budget["max_model_retries"] == 4
    assert budget["max_identical_failures"] == 5


def test_agent_policy_reads_only_the_frozen_snapshot() -> None:
    frozen = SimpleNamespace(
        snapshot=SimpleNamespace(
            budget=SnapshotBudget(
                max_steps=7,
                max_tool_calls=11,
                max_model_retries=4,
                max_identical_failures=5,
                deadline_ms=120_000,
            )
        ),
        runtime_config={
            "max_model_retries": 99,
            "max_identical_failures": 99,
        },
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
    )

    policy = agent_policy(frozen)

    assert policy.max_turns == 7
    assert policy.max_tool_calls == 11
    assert policy.max_model_retries == 4
    assert policy.max_identical_failures == 5
    assert policy.deadline_ms <= 120_000


def test_zero_max_tokens_means_unlimited_execution_budget() -> None:
    settings = _settings(max_turns=3, max_tool_calls=4)
    outcome = build_snapshot(make_materials(mode=ScheduleMode.AGENTIC), _request(), settings)

    assert outcome.snapshot is not None
    assert outcome.snapshot["budget"]["max_tokens"] == 0
