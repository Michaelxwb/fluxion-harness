"""TASK-004: pure v2 BM25 and entity/scope gates."""

from __future__ import annotations

from datetime import UTC, datetime

from muad_agent_core.memory.selection import (
    MemoryRecord,
    QueryContext,
    QueryScopeState,
    query_entity_tags,
    select_memory,
)
from muad_contracts.memory import MemoryCardinality, MemoryPriority, ScopeState

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def _record(
    key,
    *,
    title="",
    content=None,
    tags=(),
    scope_state=ScopeState.ABSENT,
    scope=None,
    predicate="general",
    memory_kind="NOTE",
    author="USER",
    priority=MemoryPriority.OPTIONAL,
):
    return MemoryRecord(
        memory_key=key,
        memory_kind=memory_kind,
        predicate=predicate,
        scope_state=scope_state,
        scope=scope or {},
        content=content or {},
        title=title,
        summary="",
        tags=tuple(tags),
        cardinality=MemoryCardinality.SET,
        priority=priority,
        author=author,
        version=1,
        updated_at=NOW,
    )


def test_title_boost_is_positive_and_not_just_length_penalty():
    title = _record("title", title="PDF", content={})
    content = _record("content", title="", content={"text": "PDF"})
    selected = select_memory(
        [content, title], QueryContext(query="PDF", now=NOW), top_k=2, min_score=0.0
    )
    assert [item.record.memory_key for item in selected] == ["title", "content"]
    assert selected[0].score > selected[1].score


def test_unknown_entity_does_not_recall_other_entity_note():
    b_only = [_record("b", title="B 客户关注数据安全", tags=("entity:customer:B", "topic:data-security"))]
    assert query_entity_tags("A 客户关注数据安全", b_only) == frozenset()
    assert select_memory(b_only, QueryContext(query="A 客户关注数据安全", now=NOW)) == ()


def test_shared_customer_does_not_mask_project_conflict():
    x = _record("a-x", title="A 客户项目 X 安全要求", tags=("entity:customer:A", "entity:project:X"))
    y = _record("a-y", title="A 客户项目 Y 安全要求", tags=("entity:customer:A", "entity:project:Y"))
    tags = query_entity_tags("A 客户项目 Y 安全要求", [x, y])
    assert tags == frozenset({"entity:customer:A", "entity:project:Y"})
    selected = select_memory([x, y], QueryContext(query="A 客户项目 Y 安全要求", now=NOW))
    assert [item.record.memory_key for item in selected] == ["a-y"]


def test_scope_unknown_suppresses_scoped_predicate_but_allows_unscoped():
    scoped = MemoryRecord(
        memory_key="scoped",
        memory_kind="PREFERENCE",
        predicate="report_format",
        scope_state=ScopeState.ABSENT,
        scope={},
        content={"format": "PDF"},
        title="报告 PDF",
        summary="",
        tags=(),
        cardinality=MemoryCardinality.SINGLE,
        priority=MemoryPriority.REQUIRED,
        author="USER",
        version=1,
        updated_at=NOW,
    )
    unscoped = MemoryRecord(
        memory_key="unscoped",
        memory_kind="PREFERENCE",
        predicate="language",
        scope_state=ScopeState.ABSENT,
        scope={},
        content={"language": "zh-CN"},
        title="语言 中文",
        summary="",
        tags=(),
        cardinality=MemoryCardinality.SINGLE,
        priority=MemoryPriority.REQUIRED,
        author="USER",
        version=1,
        updated_at=NOW,
    )
    unknown = QueryContext(query="报告 PDF 语言 中文", now=NOW, scope_state=QueryScopeState.UNKNOWN)
    selected = select_memory([scoped, unscoped], unknown)
    assert [item.record.memory_key for item in selected] == ["unscoped"]


def test_preferences_are_injected_without_a_query():
    required = _record(
        "pref-required",
        title="报告格式",
        content={"value": "PDF"},
        predicate="report_format",
        memory_kind="PREFERENCE",
        priority=MemoryPriority.REQUIRED,
    )
    optional = _record(
        "pref-optional",
        title="输出长度",
        content={"value": "short"},
        predicate="response_length",
        memory_kind="PREFERENCE",
    )
    selected = select_memory([optional, required], QueryContext(query=None, now=NOW))
    assert [item.record.memory_key for item in selected] == ["pref-required", "pref-optional"]

    matched = select_memory([optional, required], QueryContext(query="今天天气", now=NOW))
    assert [item.record.memory_key for item in matched] == ["pref-required", "pref-optional"]


def test_untagged_note_still_matches_by_topic_when_entities_are_present():
    tagged = _record(
        "tagged",
        title="A 客户数据安全要求",
        tags=("entity:customer:A", "topic:data-security"),
        predicate="general",
    )
    untagged = _record("untagged", title="数据安全备份策略", predicate="general")
    selected = select_memory([tagged, untagged], QueryContext(query="A 客户数据安全", now=NOW), top_k=5)
    assert [item.record.memory_key for item in selected] == ["tagged", "untagged"]


def test_min_score_gates_weak_matches():
    record = _record("note", title="报告格式", predicate="general")
    context = QueryContext(query="报告格式", now=NOW)
    assert select_memory([record], context, min_score=0.0)
    assert select_memory([record], context, min_score=1e9) == ()


def test_inject_inferred_false_drops_agent_rows():
    inferred = _record("inferred", title="报告格式", author="AGENT", predicate="general")
    confirmed = _record("confirmed", title="报告格式", author="USER", predicate="general")
    context = QueryContext(query="报告格式", now=NOW)
    selected = select_memory([inferred, confirmed], context, inject_inferred=False)
    assert [item.record.memory_key for item in selected] == ["confirmed"]
