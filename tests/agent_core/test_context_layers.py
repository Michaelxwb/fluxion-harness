"""Shared compaction order: summary interval, cache, and fit fallback."""

from __future__ import annotations

from collections.abc import Sequence

from muad_agent_core.context.compactor import LayerOutcome
from muad_agent_core.context.layers import SummaryAttempt, SummaryCache, compact_plan_layered
from muad_agent_core.context.plan import ContextPlan, render_context
from muad_agent_core.context.summary import SummaryFields
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_contracts.platform_settings import CompactionSettings, SummarySettings


def _fields(goal: str = "goal") -> SummaryFields:
    return SummaryFields(
        user_goal=goal,
        constraints=(),
        progress=(),
        open_items=(),
        artifacts=(),
    )


def _working_group(index: int) -> tuple[ModelMessage, ...]:
    call = ModelToolCall(f"call-{index}", "echo", {})
    return (
        ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(call,), source_event_seq=100 + index * 2),
        ModelMessage(
            role=ModelRole.TOOL,
            content="x" * 400,
            tool_call_id=call.id,
            source_event_seq=101 + index * 2,
        ),
    )


def _plan(*, material_end: int = 4, earlier: int = 1, working_groups: int = 1) -> ContextPlan:
    older = tuple(
        (
            ModelMessage(
                role=ModelRole.USER if index % 2 else ModelRole.ASSISTANT,
                content=f"old-{index}",
                source_event_seq=index,
            ),
        )
        for index in range(1, 7)
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_id="current-event",
        source_event_seq=99,
    )
    return ContextPlan(
        prefix=(
            ModelMessage(role=ModelRole.SYSTEM, content="system"),
            ModelMessage(role=ModelRole.SYSTEM, content="memory"),
            ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] old"),
        ),
        older=older,
        pinned=((current,),),
        working=tuple(_working_group(index) for index in range(working_groups)),
        covered_seq=0,
        material_end_seq=material_end,
        head_end_seq=2,
        earlier_records=earlier,
    )


def _settings(*, budget: int = 100_000) -> CompactionSettings:
    return CompactionSettings(
        history_budget_tokens=budget,
        summary=SummarySettings(enabled=True, model_ref="summary"),
    )


async def test_summary_material_is_old_summary_plus_contiguous_material_only() -> None:
    plan = _plan()
    seen: list[tuple[ModelMessage, ...]] = []
    attempts: list[SummaryAttempt] = []

    async def summarize(material: Sequence[ModelMessage]) -> SummaryFields | None:
        seen.append(tuple(material))
        return _fields()

    async def persist_summary(attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]) -> bool:
        attempts.append(attempt)
        return True

    async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
        raise AssertionError("summary success must persist through persist_summary")

    compacted, layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert [message.content for message in seen[0]] == [
        "[历史摘要] old",
        "old-1",
        "old-2",
        "old-3",
        "old-4",
    ]
    assert attempts[0].coverage_seq == 4
    assert attempts[0].protected_event_ids == ("current-event",)
    outbound = [str(message.content) for message in render_context(compacted)]
    assert "old-1" not in outbound and "old-4" not in outbound
    assert "old-5" in outbound and "old-6" in outbound, "independent tail retained"
    assert outbound.count("[历史摘要] old") == 0 and any(
        content.startswith("[历史摘要]") for content in outbound
    )
    assert any(layer.layer == "summary" for layer in layers)


async def test_summary_cache_reuses_the_same_material_without_calling_model_again() -> None:
    plan = _plan()
    cache = SummaryCache()
    calls = 0

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        nonlocal calls
        calls += 1
        return _fields()

    async def persist_summary(_attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]) -> bool:
        return True

    async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
        return None

    first, _ = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
        summary_cache=cache,
    )
    second, _ = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
        summary_cache=cache,
    )

    assert calls == 1
    assert cache.summarized_through_seq == 4
    assert render_context(first) == render_context(second)


async def test_summary_failure_falls_back_to_fit_without_advancing_coverage() -> None:
    plan = _plan()
    persisted: list[tuple[LayerOutcome, ...]] = []

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        raise RuntimeError("summary down")

    async def persist_summary(_attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]) -> bool:
        raise AssertionError("failed summary must not persist")

    async def persist_layers(layers: Sequence[LayerOutcome]) -> None:
        persisted.append(tuple(layers))

    compacted, layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert compacted.covered_seq == plan.covered_seq
    assert any(layer.layer == "budget" for layer in layers)
    assert persisted and persisted[0] == layers
    assert not any(layer.layer == "summary" for layer in layers)


async def test_summary_persist_failure_does_not_advance_coverage() -> None:
    plan = _plan()
    persisted: list[tuple[LayerOutcome, ...]] = []

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        return _fields()

    async def persist_summary(_attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]) -> bool:
        return False

    async def persist_layers(layers: Sequence[LayerOutcome]) -> None:
        persisted.append(tuple(layers))

    compacted, _layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert compacted.covered_seq == plan.covered_seq
    assert persisted
    assert not any(
        str(message.content).startswith("[历史摘要]") and "new" in str(message.content)
        for message in render_context(compacted)
    )


async def test_working_only_overflow_does_not_call_summary() -> None:
    plan = _plan(material_end=0, earlier=0, working_groups=8)
    called = False

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        nonlocal called
        called = True
        return _fields()

    async def persist_summary(_attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]) -> bool:
        raise AssertionError("working-only overflow must not summarize")

    async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
        return None

    compacted, _layers = await compact_plan_layered(
        plan,
        settings=_settings(budget=100),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert called is False
    assert compacted.working, "latest working round stays protected"
