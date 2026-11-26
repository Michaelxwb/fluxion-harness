"""TASK-005: Worker strict compaction settings and local summary cache."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from muad_agent_core.context.plan import ContextPlan
from muad_agent_core.context.summary import SummaryFields
from muad_agent_core.model import ModelMessage, ModelRole
from muad_agent_worker.application.agentic.compaction import (
    TaskContextCompactor,
    compaction_settings,
)
from muad_contracts.platform_settings import (
    CompactionConfigError,
    CompactionSettings,
    SummarySettings,
)


def _fields() -> SummaryFields:
    return SummaryFields(
        user_goal="worker goal",
        constraints=(),
        progress=(),
        open_items=(),
        artifacts=(),
    )


class _Artifacts:
    def __init__(self) -> None:
        self.writes = 0

    async def write(self, **_kwargs):
        self.writes += 1
        return None


class _Summary:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _material: Sequence[ModelMessage]) -> SummaryFields | None:
        self.calls += 1
        return _fields()


def _plan() -> ContextPlan:
    older = tuple(
        (
            ModelMessage(
                role=ModelRole.USER if index % 2 else ModelRole.ASSISTANT,
                content=f"old-{index}",
                source_event_seq=index,
            ),
        )
        for index in range(1, 4)
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_seq=99,
    )
    return ContextPlan(
        prefix=(ModelMessage(role=ModelRole.SYSTEM, content="system"),),
        older=older,
        pinned=((current,),),
        working=(),
        covered_seq=0,
        material_end_seq=3,
        head_end_seq=2,
        earlier_records=0,
    )


def _settings(*, budget: int = 1) -> CompactionSettings:
    return CompactionSettings(
        history_budget_tokens=budget,
        summary=SummarySettings(enabled=True, model_ref="worker-summary"),
    )


def test_worker_frozen_settings_are_strict() -> None:
    valid = {
        "history_budget_tokens": 12000,
        "tool_result": {"persist_threshold_bytes": 8192},
        "summary": {"enabled": False, "model_ref": None},
    }
    assert compaction_settings({"compaction": valid}).history_budget_tokens == 12000
    for broken in (
        {"compaction": {"history_budget_tokens": 12000}},
        {"compaction": {**valid, "snip": {"enabled": True}}},
        {"compaction": {**valid, "history_budget_messages": 40}},
    ):
        with pytest.raises((CompactionConfigError, ValueError)):
            compaction_settings(broken)


@pytest.mark.asyncio
async def test_worker_summary_uses_all_older_and_reuses_cache() -> None:
    summary = _Summary()
    artifacts = _Artifacts()
    compactor = TaskContextCompactor(
        _settings(),
        tenant_id="tenant",
        execution_id=uuid.uuid4(),
        summary=summary,
        artifacts=artifacts,
    )

    first = await compactor.compact_plan(_plan())
    second = await compactor.compact_plan(_plan())

    assert summary.calls == 1
    assert artifacts.writes == 1
    assert first.covered_seq == 3
    assert second.covered_seq == 3
    assert first.older == (), "summary covers all older because Worker has no durable tail"
    assert first.pinned[0][0].content == "current"
    assert first.prefix[-1].content.startswith("[历史摘要]")
