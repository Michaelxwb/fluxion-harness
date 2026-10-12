"""Pure memory selection; relevance never grants mutation permissions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory, TaskStateContent

from .tokens import lexical_coverage

_TRUST = {MemoryAuthor.USER: 2, MemoryAuthor.ADMIN: 2, MemoryAuthor.AGENT: 1}
_PREFERENCES = frozenset({MemoryCategory.PREFERENCE, MemoryCategory.WORK_STYLE})


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryRecord:
    memory_key: str
    content: FactContent | TaskStateContent
    author: MemoryAuthor
    category: MemoryCategory
    version: int
    updated_at: datetime
    last_confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    enabled: bool = True
    is_deleted: bool = False
    explicit_request: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.author, MemoryAuthor) or not isinstance(self.category, MemoryCategory):
            raise TypeError("memory author/category must be strict enums")
        _timestamp(self.updated_at)
        if self.last_confirmed_at is not None:
            _timestamp(self.last_confirmed_at)
        if self.expires_at is not None:
            _timestamp(self.expires_at)


@dataclass(frozen=True, slots=True)
class MemorySelection:
    mode: Literal["SEARCH", "LIST"]
    items: tuple[MemoryRecord, ...]


def _timestamp(value: datetime) -> float:
    if value.utcoffset() is None:
        raise ValueError("memory timestamps must be timezone aware")
    return value.timestamp()


def _valid(record: MemoryRecord, now: datetime) -> bool:
    return record.enabled and not record.is_deleted and (record.expires_at is None or record.expires_at > now)


def content_text(record: MemoryRecord) -> str:
    if isinstance(record.content, FactContent):
        return record.content.value
    state = record.content
    return "\n".join((state.title, state.goal, state.next_action, *state.constraints, *state.open_items))


def _coverage(record: MemoryRecord, query: str) -> float:
    return lexical_coverage(query, record.memory_key + "\n" + content_text(record))


def _confirmed_time(record: MemoryRecord) -> float:
    return _timestamp(record.last_confirmed_at or record.updated_at)


def _search_order(record: MemoryRecord, coverage: float) -> tuple[float, int, float, float, str]:
    return (-coverage, -_TRUST[record.author], -_confirmed_time(record), -_timestamp(record.updated_at),
            record.memory_key)


def select_memories(
    records: Iterable[MemoryRecord], *, query: str | None, now: datetime,
    category: MemoryCategory | None = None, memory_key: str | None = None,
) -> MemorySelection:
    _timestamp(now)
    candidates = [record for record in records if _valid(record, now)
                  and (category is None or record.category is category)
                  and (memory_key is None or record.memory_key == memory_key)]
    if query is None or not query.strip():
        candidates.sort(key=lambda record: (-_timestamp(record.updated_at), record.memory_key))
        return MemorySelection("LIST", tuple(candidates))
    scored = [(record, _coverage(record, query)) for record in candidates]
    matches = [(record, score) for record, score in scored if score > 0]
    matches.sort(key=lambda item: _search_order(*item))
    return MemorySelection("SEARCH", tuple(record for record, _ in matches))


def select_for_injection(
    records: Iterable[MemoryRecord], *, query: str | None, now: datetime, inject_inferred: bool,
) -> tuple[MemoryRecord, ...]:
    _timestamp(now)
    candidates = tuple(record for record in records if _valid(record, now)
                       and record.category is not MemoryCategory.TASK_STATE)
    preferences = tuple(sorted(
        (record for record in candidates if record.category in _PREFERENCES
         and record.author in (MemoryAuthor.USER, MemoryAuthor.ADMIN)),
        key=lambda record: (-_confirmed_time(record), record.memory_key),
    ))
    if query is None or not query.strip():
        return preferences
    preference_keys = {record.memory_key for record in preferences}
    related = select_memories(
        (record for record in candidates if record.memory_key not in preference_keys
         and (record.author is not MemoryAuthor.AGENT or inject_inferred)),
        query=query, now=now,
    )
    return (*preferences, *related.items)
