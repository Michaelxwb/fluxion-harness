"""TASK-005: v2 renderer keeps a notice and respects count/byte budgets."""

from __future__ import annotations

from datetime import UTC, datetime

from muad_agent_core.memory.memory_render import render_memory_v2
from muad_agent_core.memory.selection import MemoryRecordV2
from muad_contracts.memory import MemoryCardinality, MemoryPriority, ScopeState

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def _record(key, title, *, content=None, memory_kind="NOTE", predicate="general", summary="summary"):
    return MemoryRecordV2(
        memory_key=key,
        memory_kind=memory_kind,
        predicate=predicate,
        scope_state=ScopeState.ABSENT,
        scope={},
        content={"text": title} if content is None else content,
        title=title,
        summary=summary,
        tags=(),
        cardinality=MemoryCardinality.SET,
        priority=MemoryPriority.OPTIONAL,
        author="USER",
        version=1,
        updated_at=NOW,
    )


def test_renderer_includes_notice_and_drops_without_truncating_items():
    rendered = render_memory_v2([_record("a", "A" * 100), _record("b", "B" * 100)], max_records=1)
    assert "仅供参考" in rendered.text
    assert rendered.included_keys == ("a",)
    assert "B" * 100 not in rendered.text
    assert rendered.dropped_count == 1


def test_zero_budget_is_empty():
    rendered = render_memory_v2([_record("a", "A")], max_records=0)
    assert rendered.text == "" and rendered.included_keys == ()


def test_structured_content_renders_readable_fields():
    record = _record(
        "a",
        "负责系统",
        content={"value": "防火墙", "unit": "套"},
        memory_kind="FACT",
        predicate="owned_system",
        summary="",
    )
    rendered = render_memory_v2([record])
    assert "防火墙" in rendered.text and "unit=套" in rendered.text
    assert '{"value"' not in rendered.text
