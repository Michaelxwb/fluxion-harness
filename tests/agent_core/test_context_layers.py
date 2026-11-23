"""Shared compaction order: summary material, working micro, and fallback without coverage advance."""

from __future__ import annotations

import json
from collections.abc import Sequence

from muad_agent_core.context.compactor import LayerOutcome
from muad_agent_core.context.layers import SummaryAttempt, compact_plan_layered
from muad_agent_core.context.plan import ContextPlan, RunInput, plan_context, render_context
from muad_agent_core.context.summary import SummaryFields
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_contracts.platform_settings import (
    CompactionSettings,
    MicroSettings,
    SnipSettings,
    SummarySettings,
)


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
    payload = json.dumps(
        {"artifact": {"artifact_id": f"a-{index}"}, "body": "x" * 400},
        ensure_ascii=False,
    )
    return (
        ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(call,)),
        ModelMessage(role=ModelRole.TOOL, content=payload, tool_call_id=call.id),
    )


def _plan(*, working_groups: int = 4, older_count: int = 2) -> ContextPlan:
    history: list[ModelMessage] = [
        ModelMessage(role=ModelRole.SYSTEM, content="system"),
        ModelMessage(role=ModelRole.SYSTEM, content="memory"),
        ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] old"),
    ]
    for index in range(older_count):
        history.append(
            ModelMessage(
                role=ModelRole.USER if index % 2 == 0 else ModelRole.ASSISTANT,
                content=f"old-{index}",
                source_event_seq=index + 1,
            )
        )
    history.append(
        ModelMessage(
            role=ModelRole.USER,
            content="current",
            message_id="current",
            source_event_id="current-event",
            source_event_seq=99,
        )
    )
    for index in range(working_groups):
        history.extend(_working_group(index))
    return plan_context(
        tuple(history),
        run_input=RunInput(message_id="current", content="current"),
    )


def _settings() -> CompactionSettings:
    return CompactionSettings(
        snip=SnipSettings(
            enabled=True, max_groups=1, keep_head_groups=0, keep_tail_groups=1
        ),
        micro=MicroSettings(enabled=True, keep_recent_tool_groups=1),
        summary=SummarySettings(enabled=True, threshold_bytes=1, model_ref="summary"),
    )


async def test_summary_material_is_old_summary_plus_original_older_only() -> None:
    plan = _plan()
    seen: list[tuple[ModelMessage, ...]] = []
    attempts: list[SummaryAttempt] = []

    async def summarize(material: Sequence[ModelMessage]) -> SummaryFields | None:
        seen.append(tuple(material))
        return _fields()

    async def persist_summary(
        attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]
    ) -> bool:
        attempts.append(attempt)
        return True

    async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
        raise AssertionError("summary success must not run the fallback persister")

    compacted, layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert [message.content for message in seen[0]] == [
        "[历史摘要] old",
        "old-0",
        "old-1",
    ], "摘要材料只含旧摘要与完整原始 older"
    assert attempts and attempts[0].material == seen[0]
    assert attempts[0].coverage_seq == plan.coverage_seq == 2
    assert attempts[0].protected_event_ids == ("current-event",)

    outbound = render_context(compacted)
    assert [message.content for message in outbound[:2]] == ["system", "memory"]
    assert outbound[2].content.startswith("[历史摘要]")
    assert outbound[3].content == "current"
    assert sum(
        str(message.content).startswith("[历史摘要]") for message in outbound
    ) == 1, "新摘要替换旧摘要，不是追加"
    assert compacted.older == (), "摘要成功才清空 older"
    assert len(compacted.working) == len(plan.working), "working 不参与 snip/trim"
    degraded = sum(
        "工具结果已外置" in str(message.content)
        for group in compacted.working
        for message in group
    )
    assert degraded == 3, "working 只做 micro：保留最近 1 组，降级其余 3 组"
    assert any(layer.layer == "summary" and layer.fired for layer in layers)


async def test_summary_persist_failure_falls_back_without_advancing_coverage() -> None:
    plan = _plan(older_count=6)
    persisted: list[tuple[LayerOutcome, ...]] = []

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        return _fields()

    async def persist_summary(
        _attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]
    ) -> bool:
        return False

    async def persist_layers(layers: Sequence[LayerOutcome]) -> None:
        persisted.append(tuple(layers))

    compacted, layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    summaries = [
        message for message in render_context(compacted) if str(message.content).startswith("[历史摘要]")
    ]
    assert [str(message.content) for message in summaries] == ["[历史摘要] old"], (
        "持久化失败 ⇒ 新摘要作废，旧摘要原样保留"
    )
    assert compacted.coverage_seq == plan.coverage_seq, "coverage 不得推进"
    assert len(compacted.older) < len(plan.older), "回退到原始 older 的 micro/snip/trim"
    assert persisted and persisted[0] == layers
    assert any(layer.layer == "snip" and layer.fired for layer in layers)
    assert any(layer.layer == "micro" and layer.fired for layer in layers)
    assert not any(layer.layer == "summary" for layer in layers)


async def test_summary_call_exception_degrades_to_the_fallback_layers() -> None:
    plan = _plan(older_count=4)
    persisted: list[tuple[LayerOutcome, ...]] = []

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        raise RuntimeError("summary model down")

    async def persist_summary(
        _attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]
    ) -> bool:
        raise AssertionError("rejected summary must not be persisted")

    async def persist_layers(layers: Sequence[LayerOutcome]) -> None:
        persisted.append(tuple(layers))

    compacted, layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert persisted and persisted[0] == layers
    assert not any(layer.layer == "summary" for layer in layers)
    assert compacted.coverage_seq == plan.coverage_seq
    assert any(layer.layer == "snip" and layer.fired for layer in layers)


async def test_working_is_never_snipped_or_trimmed() -> None:
    history = [
        ModelMessage(role=ModelRole.SYSTEM, content="system"),
        ModelMessage(role=ModelRole.USER, content="current", message_id="current"),
    ]
    for index in range(6):
        history.extend(_working_group(index))
    plan = plan_context(
        tuple(history),
        run_input=RunInput(message_id="current", content="current"),
    )

    async def persist_layers(_layers: Sequence[LayerOutcome]) -> None:
        return None

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        raise AssertionError("no older interval ⇒ no summary material")

    async def persist_summary(
        _attempt: SummaryAttempt, _layers: Sequence[LayerOutcome]
    ) -> bool:
        raise AssertionError("no older interval ⇒ no summary")

    compacted, _layers = await compact_plan_layered(
        plan,
        settings=_settings(),
        summarize=summarize,
        persist_summary=persist_summary,
        persist_layers=persist_layers,
    )

    assert len(compacted.working) == len(plan.working)
    assert not any(
        "[历史省略]" in str(message.content)
        for group in compacted.working
        for message in group
    )
