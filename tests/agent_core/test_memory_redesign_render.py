"""B-09 support: final envelopes, including metadata, obey UTF-8 limits."""

import json
from datetime import UTC, datetime
from importlib import import_module
from uuid import uuid4

import pytest
from muad_agent_core.memory.rank import MemoryRecord
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory, TaskStateContent, task_memory_key

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def _record(key="language", value="请使用中文", author=MemoryAuthor.USER):
    return MemoryRecord(
        memory_key=key, content=FactContent(value=value), author=author,
        category=MemoryCategory.PREFERENCE, version=3, updated_at=NOW,
    )


def _render(rows, max_records=10, max_bytes=16384):
    return import_module("muad_agent_core.memory.render").render_memory_block(
        rows, max_records=max_records, max_bytes=max_bytes
    )


def test_b09_complete_chinese_block_equal_budget_fits_one_byte_less_drops():
    row = _record()
    full = _render([row])
    size = len(full.text.encode("utf-8"))
    assert _render([row], max_bytes=size).text == full.text
    small = _render([row], max_bytes=size - 1)
    assert small.text is None and small.items == ()
    assert small.dropped_bytes == 1 and small.dropped_count == 0
    assert "以下为既往记忆" in full.text and full.text.count("以下为既往记忆") == 1
    assert '"version":3' in full.text and '"memory_key":"language"' in full.text


@pytest.mark.parametrize(("max_records", "max_bytes"), [(0, 16384), (10, 0)])
def test_b09_zero_budget_has_no_placeholder(max_records, max_bytes):
    result = _render([_record()], max_records, max_bytes)
    assert result.text is None and result.items == ()


def test_b09_long_entry_drops_but_later_short_one_still_fits():
    short = _record("short", "好")
    budget = len(_render([short]).text.encode("utf-8"))
    result = _render([_record("long", "甲" * 512), short], max_bytes=budget)
    assert [item.record.memory_key for item in result.items] == ["short"]
    assert len(result.text.encode("utf-8")) == budget
    assert result.dropped_bytes == 1


def test_b09_each_dropped_entry_has_only_one_reason():
    result = _render([_record("a"), _record("b"), _record("c")], max_records=1)
    assert len(result.items) == 1 and result.dropped_count == 2 and result.dropped_bytes == 0
    assert _render([]).text is None


def test_b09_inferred_and_confirmed_sections_and_escaped_values():
    result = _render([_record("confirmed"), _record("inferred", 'line\n"fake"', MemoryAuthor.AGENT)])
    assert "confirmed" in result.text and "inferred" in result.text
    assert 'line\\n\\"fake\\"' in result.text


def test_task_state_falls_back_to_reference_then_drops_when_reference_cannot_fit():
    root = uuid4()
    state = TaskStateContent(
        title="季度报告", goal="甲" * 200, constraints=[], open_items=["补数据"],
        next_action="乙" * 200, root_conversation_id=root, source_conversation_id=root,
    )
    row = MemoryRecord(
        memory_key=task_memory_key(root), content=state, author=MemoryAuthor.AGENT,
        category=MemoryCategory.TASK_STATE, version=4, updated_at=NOW,
    )
    full = _render([row])
    result = _render([row], max_bytes=len(full.text.encode("utf-8")) - 1)
    assert len(result.items) == 1 and result.items[0].omitted is True
    assert '"omitted":true' in result.text and row.memory_key in result.text
    assert "未确认" in result.text and "recall" in result.text
    assert "甲" * 200 not in result.text
    tiny = _render([row], max_bytes=1)
    assert tiny.text is None and tiny.dropped_bytes == 1


def test_recall_bounds_complete_json_without_truncating_values():
    renderer = import_module("muad_agent_core.memory.render")
    rows = [_record("long", "甲" * 512), _record("short", "好")]
    full = renderer.render_recall([rows[1]], limit=10, max_bytes=16384)
    size = len(full.text.encode("utf-8"))
    assert renderer.render_recall([rows[1]], limit=10, max_bytes=size).text == full.text
    result = renderer.render_recall(rows, limit=10, max_bytes=size)
    data = json.loads(result.text)
    assert len(result.text.encode("utf-8")) <= size
    assert data["truncated"] is True
    assert [item["memory_key"] for item in data["items"]] == ["short"]
    assert data["items"][0]["content"] == {"value": "好"}
    assert "author" in data["items"][0] and "expires_at" in data["items"][0]


def test_recall_zero_limit_and_impossible_fixed_envelope():
    renderer = import_module("muad_agent_core.memory.render")
    assert json.loads(renderer.render_recall([_record()], limit=0, max_bytes=4096).text)["items"] == []
    with pytest.raises(renderer.MemoryResultBudgetExceeded):
        renderer.render_recall([], limit=10, max_bytes=1)


def test_memory_component_preserves_typed_identity_and_empty_message():
    components = import_module("muad_agent_core.memory.components")
    component = components.MemoryComponent(snapshot_revision=5, block=_render([_record()]))
    assert component.name == "memory" and component.snapshot_revision == 5
    assert component.references[0].memory_key == "language"
    assert component.references[0].version == 3
    assert component.message.role == "system" and component.message.content == component.block.text
    assert components.MemoryComponent(snapshot_revision=6, block=_render([])).message is None
