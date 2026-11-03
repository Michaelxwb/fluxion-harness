"""S-07 / B-01 / B-02 / B-03: memory selection gating, budget, versions, precompute (TASK-007)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from muad_agent_core.memory import rank
from muad_agent_core.memory.rank import (
    MemoryRecord,
    record_tokens,
    select_for_injection,
)
from muad_agent_core.memory.render import render_memory_block
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory

NOW = datetime.now(UTC)


def _record(
    key: str,
    value: str,
    *,
    category: MemoryCategory = MemoryCategory.EXPLICIT,
    author: MemoryAuthor = MemoryAuthor.USER,
    version: int = 1,
    activation_state: str = "ACTIVE",
    task_signature: str | None = None,
    trigger_text: str | None = None,
    confirmed_minutes_ago: int = 0,
    precompute: bool = True,
) -> MemoryRecord:
    record = MemoryRecord(
        memory_key=key,
        content=FactContent(value=value),
        author=author,
        category=category,
        version=version,
        updated_at=NOW,
        last_confirmed_at=NOW - timedelta(minutes=confirmed_minutes_ago),
        activation_state=activation_state,
        task_signature=task_signature,
        trigger_text=trigger_text,
    )
    return _with_tokens(record) if precompute else record


def _with_tokens(record: MemoryRecord) -> MemoryRecord:
    from dataclasses import replace

    return replace(record, tokens=record_tokens(record))


def test_s07_gating_excludes_low_score_and_empty_query_facts() -> None:
    records = [
        _record("fact.customer-format", "客户报表用表格"),
        _record("fact.weather", "明天天气晴"),
        _record("pref.briefing", "简报先给结论", category=MemoryCategory.PREFERENCE, confirmed_minutes_ago=1),
        _record("pref.style", "回答用中文", category=MemoryCategory.WORK_STYLE, confirmed_minutes_ago=2),
        _record("pref.extra-1", "偏好一", category=MemoryCategory.PREFERENCE, confirmed_minutes_ago=3),
        _record("pref.extra-2", "偏好二", category=MemoryCategory.PREFERENCE, confirmed_minutes_ago=4),
    ]
    selected = select_for_injection(
        records, query="客户报表", now=NOW, inject_inferred=True, preference_items=3
    )
    keys = [record.memory_key for record in selected]
    assert "fact.customer-format" in keys
    assert "fact.weather" not in keys
    assert keys[:3] == ["pref.briefing", "pref.style", "pref.extra-1"]

    empty = select_for_injection(records, query=None, now=NOW, inject_inferred=True, preference_items=3)
    assert [record.memory_key for record in empty] == [
        "pref.briefing",
        "pref.style",
        "pref.extra-1",
    ]
    assert all(record.category in (MemoryCategory.PREFERENCE, MemoryCategory.WORK_STYLE) for record in empty)

    again = select_for_injection(records, query="客户报表", now=NOW, inject_inferred=True, preference_items=3)
    assert [record.memory_key for record in again] == keys


def test_b01_budget_and_drop_counters() -> None:
    records = [_record(f"fact.item-{index}", "客户报表 " + "x" * 80) for index in range(5)]
    preferences = [
        _record(
            f"pref.item-{index}",
            "偏好" + "y" * 300,
            category=MemoryCategory.PREFERENCE,
            confirmed_minutes_ago=index,
        )
        for index in range(4)
    ]
    selected = select_for_injection(
        records + preferences,
        query="客户报表",
        now=NOW,
        inject_inferred=True,
        preference_items=3,
        preference_bytes=650,
    )
    prefs = [record for record in selected if record.category is MemoryCategory.PREFERENCE]
    assert len(prefs) <= 3
    assert sum(len(record.content.value.encode()) for record in prefs) <= 650

    block = render_memory_block(selected, max_records=2, max_bytes=16384)
    assert len(block.items) <= 2
    assert block.dropped_count >= len(selected) - len(block.items)


def test_b02_versions_only_latest_is_injected() -> None:
    records = [
        _record("fact.customer-format", "客户报表用表格", version=1),
        _record("fact.customer-format", "客户报表用 Markdown 表格", version=2),
    ]
    selected = select_for_injection(records, query="客户报表", now=NOW, inject_inferred=True)
    assert [record.version for record in selected] == [2]


def test_b03_upper_bound_selection_stays_in_process(monkeypatch) -> None:
    records = [_record(f"fact.item-{index}", f"客户报表 {index}") for index in range(200)]
    upper = [_record(f"fact.bulk-{index}", f"客户报表批次 {index}") for index in range(1000)]

    calls: list[str] = []
    original = rank.tokenize

    def counting(text: str):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(rank, "tokenize", counting)
    selected = select_for_injection(records + upper, query="客户报表", now=NOW, inject_inferred=True)
    # 候选 token 集已预计算：请求期只 token 化查询本身。
    assert calls == ["客户报表"]
    assert isinstance(selected, tuple)
