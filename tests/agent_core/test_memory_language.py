"""Reply-language policy: memory preference > platform default, resolved once."""

from __future__ import annotations

from datetime import UTC, datetime

from muad_agent_core.memory.components import MemoryComponent
from muad_agent_core.memory.language import memory_language, resolve_reply_locale
from muad_agent_core.memory.selection import MemoryRecord
from muad_contracts.memory import MemoryCardinality, MemoryPriority, ScopeState

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def _record(
    *,
    key: str = "language",
    value: object = "zh-CN",
    memory_kind: str = "PREFERENCE",
    predicate: str = "language",
) -> MemoryRecord:
    return MemoryRecord(
        memory_key=key,
        memory_kind=memory_kind,
        predicate=predicate,
        scope_state=ScopeState.ABSENT,
        scope={},
        content={"value": value},
        title="language",
        summary="",
        tags=(),
        cardinality=MemoryCardinality.SET,
        priority=MemoryPriority.OPTIONAL,
        author="USER",
        version=1,
        updated_at=NOW,
    )


def test_memory_language_reads_the_preference_value() -> None:
    assert memory_language([_record(value="zh-CN")]) == "zh-CN"
    assert memory_language([_record(value="en-US")]) == "en-US"
    assert memory_language([_record(value="zh")]) == "zh-CN"
    assert memory_language([_record(value="en")]) == "en-US"


def test_memory_language_ignores_non_language_or_invalid_records() -> None:
    assert memory_language([_record(memory_kind="NOTE")]) is None
    assert memory_language([_record(predicate="timezone")]) is None
    assert memory_language([_record(value="fr-FR")]) is None
    assert memory_language([_record(value=123)]) is None


def test_memory_language_uses_selection_order() -> None:
    records = [_record(key="first", value="en-US"), _record(key="second", value="zh-CN")]
    assert memory_language(records) == "en-US"


def test_resolve_reply_locale_prefers_memory_then_platform() -> None:
    assert resolve_reply_locale("en-US", "zh-CN") == "zh-CN"
    assert resolve_reply_locale("en-US", None) == "en-US"
    assert resolve_reply_locale("en-US", "fr-FR") == "en-US"
    assert resolve_reply_locale(None, None) is None


def test_memory_component_language_defaults_to_none() -> None:
    component = MemoryComponent(snapshot_revision=1, text=None)

    assert component.language is None
