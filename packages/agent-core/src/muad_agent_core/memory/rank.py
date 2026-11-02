"""Pure memory selection; relevance never grants mutation permissions."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory, TaskStateContent

from .tokens import lexical_coverage, tokenize

_TRUST = {MemoryAuthor.USER: 2, MemoryAuthor.ADMIN: 2, MemoryAuthor.AGENT: 1}
_PREFERENCES = frozenset({MemoryCategory.PREFERENCE, MemoryCategory.WORK_STYLE})
_EXPERIENCE = frozenset(
    {MemoryCategory.PROCEDURE, MemoryCategory.FAILURE_PATTERN, MemoryCategory.VERIFICATION}
)


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
    activation_state: str = "ACTIVE"
    task_signature: str | None = None
    trigger_text: str | None = None
    #: 缓存装载时预计算的 token 集；请求期选择不再重新 token 化（NFR-PERF-01）。
    tokens: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.author, MemoryAuthor) or not isinstance(self.category, MemoryCategory):
            raise TypeError("memory author/category must be strict enums")
        _timestamp(self.updated_at)
        if self.last_confirmed_at is not None:
            _timestamp(self.last_confirmed_at)
        if self.expires_at is not None:
            _timestamp(self.expires_at)


@dataclass(frozen=True, slots=True)
class SelectedMemory:
    record: MemoryRecord
    score: float
    channel: str


@dataclass(frozen=True, slots=True)
class SkippedMemory:
    memory_key: str
    activation_state: str
    reason: str
    score: float | None = None


@dataclass(frozen=True, slots=True)
class InjectionSelection:
    items: tuple[SelectedMemory, ...]
    skipped: tuple[SkippedMemory, ...]

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(item.record for item in self.items)


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


def record_tokens(record: MemoryRecord) -> frozenset[str]:
    if record.tokens is not None:
        return record.tokens
    text = record.memory_key + "\n" + content_text(record)
    if record.trigger_text:
        text = text + "\n" + record.trigger_text
    return tokenize(text)


def _confirmed_time(record: MemoryRecord) -> float:
    return _timestamp(record.last_confirmed_at or record.updated_at)


def score_record(record: MemoryRecord, query: str | None) -> float:
    """注入归因用的单条分数：偏好类在空查询下记 1.0，其余按词法覆盖率。"""
    if query is None or not query.strip():
        return 1.0 if record.category in _PREFERENCES else 0.0
    return _coverage(record, query)


def _idf(
    token_sets: list[frozenset[str]], query_tokens: frozenset[str]
) -> tuple[dict[str, float], float]:
    if not query_tokens:
        return {}, 0.0
    total_documents = len(token_sets)
    document_frequency = {
        token: sum(1 for tokens in token_sets if token in tokens) for token in query_tokens
    }
    weights = {
        token: math.log((total_documents + 1) / (document_frequency[token] + 1)) + 1.0
        for token in query_tokens
    }
    return weights, sum(weights.values())


def _weighted_coverage(
    record: MemoryRecord,
    query_tokens: frozenset[str],
    weights: dict[str, float],
    total: float,
) -> float:
    if not query_tokens or total <= 0:
        return 0.0
    overlap = query_tokens & record_tokens(record)
    return sum(weights[token] for token in overlap) / total


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


def select_injection(
    records: Iterable[MemoryRecord],
    *,
    query: str | None,
    now: datetime,
    inject_inferred: bool,
    task_family: str | None = None,
    min_score: float = 0.35,
    preference_items: int = 3,
    preference_bytes: int = 512,
    allowed_experience_keys: frozenset[str] | None = None,
) -> InjectionSelection:
    """阈值门控注入：偏好独立限额，经验走任务族/目标文本两通道，同键只取最新。"""
    _timestamp(now)
    if min_score < 0 or preference_items < 0 or preference_bytes < 0:
        raise ValueError("invalid injection policy")

    skipped: list[SkippedMemory] = []
    latest: dict[str, MemoryRecord] = {}
    for record in records:
        if record.category is MemoryCategory.TASK_STATE:
            continue
        if not _valid(record, now):
            if record.category in _EXPERIENCE:
                reason = "EXPIRED" if record.expires_at is not None else "DISABLED"
                skipped.append(SkippedMemory(record.memory_key, record.activation_state, reason))
            continue
        current = latest.get(record.memory_key)
        if current is None or record.version > current.version:
            latest[record.memory_key] = record
    candidates = tuple(latest.values())

    preferences: list[MemoryRecord] = []
    used_bytes = 0
    for record in sorted(
        (
            record
            for record in candidates
            if record.category in _PREFERENCES and record.author in (MemoryAuthor.USER, MemoryAuthor.ADMIN)
        ),
        key=lambda record: (-_confirmed_time(record), record.memory_key),
    ):
        size = len(content_text(record).encode("utf-8"))
        if len(preferences) >= preference_items or used_bytes + size > preference_bytes:
            continue
        preferences.append(record)
        used_bytes += size
    if query is None or not query.strip():
        for record in candidates:
            if record.category in _EXPERIENCE and record.memory_key not in {
                item.memory_key for item in preferences
            }:
                skipped.append(
                    SkippedMemory(record.memory_key, record.activation_state, "EMPTY_QUERY")
                )
        return InjectionSelection(
            tuple(SelectedMemory(record, 1.0, "PREFERENCE") for record in preferences),
            tuple(skipped),
        )

    query_tokens = tokenize(query)
    weights, total = _idf([record_tokens(record) for record in candidates], query_tokens)
    preference_keys = {record.memory_key for record in preferences}
    selected: list[tuple[MemoryRecord, float, str]] = []
    for record in candidates:
        if record.memory_key in preference_keys:
            continue
        score = _weighted_coverage(record, query_tokens, weights, total)
        if record.category in _EXPERIENCE:
            if record.activation_state != "ACTIVE":
                skipped.append(
                    SkippedMemory(record.memory_key, record.activation_state, "NOT_ACTIVE", score)
                )
                continue
            if (
                allowed_experience_keys is not None
                and record.memory_key not in allowed_experience_keys
            ):
                skipped.append(
                    SkippedMemory(
                        record.memory_key, record.activation_state, "NOT_IN_FROZEN_SET", score
                    )
                )
                continue
            family_hit = task_family is not None and record.task_signature == task_family
            if family_hit:
                selected.append((record, 1.0, "FAMILY"))
                continue
            trigger = record.trigger_text or ""
            goal_score = lexical_coverage(query, trigger + "\n" + content_text(record))
            if goal_score >= min_score:
                selected.append((record, goal_score, "TEXT"))
            else:
                skipped.append(
                    SkippedMemory(record.memory_key, record.activation_state, "BELOW_THRESHOLD", goal_score)
                )
            continue
        if record.author is MemoryAuthor.AGENT and not inject_inferred:
            continue
        if score >= min_score:
            selected.append((record, score, "TEXT"))
    selected.sort(key=lambda item: _search_order(item[0], item[1]))
    items = tuple(SelectedMemory(record, 1.0, "PREFERENCE") for record in preferences) + tuple(
        SelectedMemory(record, score, channel) for record, score, channel in selected
    )
    return InjectionSelection(items, tuple(skipped))


def select_for_injection(
    records: Iterable[MemoryRecord],
    *,
    query: str | None,
    now: datetime,
    inject_inferred: bool,
    task_family: str | None = None,
    min_score: float = 0.35,
    preference_items: int = 3,
    preference_bytes: int = 512,
    allowed_experience_keys: frozenset[str] | None = None,
) -> tuple[MemoryRecord, ...]:
    """兼容旧调用方：只返回选中记录；诊断请用 `select_injection`。"""
    return select_injection(
        records,
        query=query,
        now=now,
        inject_inferred=inject_inferred,
        task_family=task_family,
        min_score=min_score,
        preference_items=preference_items,
        preference_bytes=preference_bytes,
        allowed_experience_keys=allowed_experience_keys,
    ).records
