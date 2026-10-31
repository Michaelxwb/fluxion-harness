"""S-11: labeled calibration set for the threshold gate (TASK-007)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from muad_agent_core.memory.rank import MemoryRecord, record_tokens, select_for_injection
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory

NOW = datetime.now(UTC)


def _record(key: str, value: str, category=MemoryCategory.EXPLICIT) -> MemoryRecord:
    record = MemoryRecord(
        memory_key=key,
        content=FactContent(value=value),
        author=MemoryAuthor.USER,
        category=category,
        version=1,
        updated_at=NOW,
        last_confirmed_at=NOW,
    )
    return replace(record, tokens=record_tokens(record))


def test_s11_threshold_gate_keeps_recall_and_cuts_noise() -> None:
    positives = [
        _record("fact.customer-format", "客户报表用表格"),
        _record("fact.customer-table", "客户报表需要按客户分行"),
    ]
    negatives = [
        _record("fact.weekly", "周报模板在共享盘"),
        _record("fact.archive", "报表已归档"),
    ]
    preference = _record("pref.briefing", "简报先给结论", MemoryCategory.PREFERENCE)
    records = [*positives, *negatives, preference]

    selected = select_for_injection(records, query="客户报表", now=NOW, inject_inferred=True)
    keys = {record.memory_key for record in selected}

    assert {record.memory_key for record in positives} <= keys
    assert not ({record.memory_key for record in negatives} & keys)
    assert "pref.briefing" in keys
    # 现状（有重叠即注入）会把"报表已归档"也带进来；阈值门控后注入条目数下降。
    legacy = [record for record in records if record.memory_key.startswith("fact.")]
    assert len([record for record in legacy if record.memory_key in keys]) == 2

    again = select_for_injection(records, query="客户报表", now=NOW, inject_inferred=True)
    assert [record.memory_key for record in again] == [record.memory_key for record in selected]
