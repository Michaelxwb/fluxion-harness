"""B-02: actual lexical matches precede trust and deterministic tie breaking."""

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory

NOW = datetime(2026, 10, 12, tzinfo=UTC)
pytestmark = pytest.mark.unit


def _record(key, value, author=MemoryAuthor.USER, category=MemoryCategory.EXPLICIT, **kwargs):
    rank = import_module("muad_agent_core.memory.rank")
    return rank.MemoryRecord(
        memory_key=key, content=FactContent(value=value), author=author, category=category,
        version=1, updated_at=NOW, **kwargs,
    )


@pytest.mark.parametrize(("text", "tokens"), [
    ("Use PYTHON, python 3", {"use", "python", "3"}),
    ("喜欢中文", {"喜欢", "欢中", "中文"}),
    ("Python中文", {"python", "中文"}),
    ("中", set()), (" \t!?", set()), ("ＡＢＣ", {"abc"}),
])
def test_b02_tokenize_cjk_bigrams_and_latin_words(text, tokens):
    lexical = import_module("muad_agent_core.memory.tokens")
    assert lexical.tokenize(text) == frozenset(tokens)


def test_b02_coverage_uses_query_terms_and_never_synonym_guesses():
    lexical = import_module("muad_agent_core.memory.tokens")
    assert lexical.lexical_coverage("python rust", "python") == 0.5
    assert lexical.lexical_coverage("python", "python python") == 1.0
    assert lexical.lexical_coverage("蓝色", "喜欢红色") == 0.0
    assert lexical.lexical_coverage("中", "中文") == 0.0
    assert lexical.lexical_coverage("", "anything") == 0.0


@pytest.mark.parametrize("query", ["rust", "中", "???"])
def test_b02_nonempty_zero_coverage_is_empty_search(query):
    rank = import_module("muad_agent_core.memory.rank")
    result = rank.select_memories([_record("language", "python")], query=query, now=NOW)
    assert result.mode == "SEARCH"
    assert result.items == ()


@pytest.mark.parametrize("query", [None, "", "  \n"])
def test_b02_list_is_distinct_from_search_and_deterministic(query):
    rank = import_module("muad_agent_core.memory.rank")
    rows = [_record("b", "python"), _record("a", "rust")]
    result = rank.select_memories(rows, query=query, now=NOW)
    assert result.mode == "LIST"
    assert [row.memory_key for row in result.items] == ["a", "b"]


def test_b02_search_coverage_precedes_trust_and_ties_use_key():
    rank = import_module("muad_agent_core.memory.rank")
    rows = [
        _record("user", "python"), _record("agent-b", "python rust", author=MemoryAuthor.AGENT),
        _record("agent-a", "python rust", author=MemoryAuthor.AGENT),
    ]
    result = rank.select_memories(rows, query="python rust", now=NOW)
    assert [row.memory_key for row in result.items] == ["agent-a", "agent-b", "user"]
    assert rank.select_memories(reversed(rows), query="python rust", now=NOW) == result


def test_b02_preferences_ignore_query_and_require_confirmed_author():
    rank = import_module("muad_agent_core.memory.rank")
    rows = [
        _record("user", "中文", category=MemoryCategory.PREFERENCE),
        _record("agent", "中文", author=MemoryAuthor.AGENT, category=MemoryCategory.PREFERENCE),
        _record("admin", "简洁", author=MemoryAuthor.ADMIN, category=MemoryCategory.WORK_STYLE),
        _record("fact", "rust"),
    ]
    selected = rank.select_for_injection(rows, query="你好", now=NOW, inject_inferred=False)
    assert [row.memory_key for row in selected] == ["admin", "user"]
    assert rank.select_for_injection(rows, query=None, now=NOW, inject_inferred=True) == selected
    matched = rank.select_for_injection(rows, query="中文", now=NOW, inject_inferred=True)
    assert [row.memory_key for row in matched] == ["admin", "user", "agent"]


def test_b02_category_key_and_current_ttl_are_filters_before_ranking():
    rank = import_module("muad_agent_core.memory.rank")
    rows = [
        _record("active", "python", category=MemoryCategory.PREFERENCE),
        _record("expired", "python", expires_at=NOW),
        _record("disabled", "python", enabled=False),
        _record("deleted", "python", is_deleted=True),
        _record("soon", "python", expires_at=NOW + timedelta(seconds=1)),
    ]
    assert [row.memory_key for row in rank.select_memories(rows, query="python", now=NOW).items] == [
        "active", "soon"
    ]
    assert len(rank.select_memories(rows, query=None, now=NOW, category=MemoryCategory.PREFERENCE).items) == 1
    assert rank.select_memories(rows, query=None, now=NOW, memory_key="expired").items == ()
    assert rank.select_memories(
        rows, query="python", now=NOW + timedelta(seconds=2), memory_key="soon"
    ).items == ()


def test_b02_structural_json_keys_are_not_searchable_content():
    rank = import_module("muad_agent_core.memory.rank")
    assert rank.select_memories([_record("language", "python")], query="value", now=NOW).items == ()


def test_b02_record_rejects_untyped_source_and_naive_time():
    with pytest.raises(TypeError, match="strict enums"):
        _record("key", "value", author="USER")
    rank = import_module("muad_agent_core.memory.rank")
    with pytest.raises(ValueError, match="timezone aware"):
        rank.select_memories([], query=None, now=datetime(2026, 10, 12))
