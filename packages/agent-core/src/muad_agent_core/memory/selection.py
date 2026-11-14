"""Pure v2 retrieval: binary BM25, scope/entity gates, deterministic ordering."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from muad_contracts.canonical import canonical_json
from muad_contracts.memory import MemoryCardinality, MemoryPriority, ScopeState
from enum import StrEnum
from muad_contracts.memory_taxonomy import PREDICATES_BY_KEY, entity_aliases

from .tokens import tokenize

_FIELD_WEIGHTS = {
    "title": 2.0,
    "summary": 1.5,
    "content_text": 1.0,
    "topic_tags": 1.5,
    "predicate_aliases": 1.0,
}


@dataclass(frozen=True, slots=True)
class MemoryRecordV2:
    memory_key: str
    memory_kind: str
    predicate: str
    scope_state: ScopeState
    scope: dict[str, str]
    content: dict[str, object]
    title: str
    summary: str
    tags: tuple[str, ...]
    cardinality: MemoryCardinality
    priority: MemoryPriority
    author: str
    version: int
    updated_at: datetime
    expires_at: datetime | None = None
    enabled: bool = True
    is_deleted: bool = False


class QueryScopeState(StrEnum):
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    EXPLICIT = "EXPLICIT"


@dataclass(frozen=True, slots=True)
class QueryContextV2:
    query: str | None
    now: datetime
    scope_state: QueryScopeState = QueryScopeState.ABSENT
    scope: dict[str, str] | None = None
    #: 由 taxonomy 别名在候选集合上命中的实体标签；调用方给定时不再自行推断。
    entity_tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SelectedV2:
    record: MemoryRecordV2
    score: float


def _tokens(text: str) -> frozenset[str]:
    return tokenize(text)


def _field_tokens(record: MemoryRecordV2) -> dict[str, frozenset[str]]:
    tags = " ".join(tag.removeprefix("topic:") for tag in record.tags if tag.startswith("topic:"))
    aliases = " ".join(PREDICATES_BY_KEY.get(record.predicate).aliases) if record.predicate in PREDICATES_BY_KEY else ""
    return {
        "title": _tokens(record.title),
        "summary": _tokens(record.summary),
        "content_text": _tokens(canonical_json(record.content)),
        "topic_tags": _tokens(tags),
        "predicate_aliases": _tokens(aliases),
    }


def binary_bm25(record: MemoryRecordV2, query_tokens: frozenset[str], *, average_length: float) -> float:
    fields = _field_tokens(record)
    lengths = {name: len(tokens) for name, tokens in fields.items()}
    weighted_length = len(frozenset().union(*fields.values()))
    if not query_tokens:
        return 0.0
    # Caller supplies document-frequency/idf-normalized query tokens via score_records;
    # this helper is exposed for the invariant test and uses a unit idf.
    score = 0.0
    for token in query_tokens:
        weight = max(
            (_FIELD_WEIGHTS[name] for name, tokens in fields.items() if token in tokens),
            default=0.0,
        )
        if not weight:
            continue
        score += weight / (1 + 1.2 * (1 - 0.75 + 0.75 * weighted_length / max(1.0, average_length)))
    return score / (len(query_tokens) or 1)


def _score_records(records: list[MemoryRecordV2], query: str) -> dict[str, float]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return {}
    field_tokens = [_field_tokens(record) for record in records]
    lengths = [len(frozenset().union(*fields.values())) for fields in field_tokens]
    average = max(1.0, sum(lengths) / len(lengths)) if lengths else 1.0
    total = len(records)
    scores: dict[str, float] = {}
    for record, fields, length in zip(records, field_tokens, lengths, strict=True):
        df = {token: sum(1 for field in field_tokens if token in frozenset().union(*field.values())) for token in query_tokens}
        score = 0.0
        for token in query_tokens:
            weight = max(
                (_FIELD_WEIGHTS[name] for name, tokens in fields.items() if token in tokens),
                default=0.0,
            )
            if not weight:
                continue
            idf = math.log(1 + (total - df[token] + 0.5) / (df[token] + 0.5))
            score += weight * idf * 2.2 / (1 + 1.2 * (1 - 0.75 + 0.75 * length / average))
        if score > 0:
            scores[record.memory_key] = score
    return scores


def query_entity_tags(query: str | None, records: list[MemoryRecordV2]) -> frozenset[str]:
    if not query:
        return frozenset()
    normalized = query.casefold()
    matched: set[str] = set()
    for record in records:
        for tag in record.tags:
            if not tag.startswith("entity:"):
                continue
            try:
                _, entity_type, entity_id = tag.split(":", 2)
            except ValueError:
                continue
            if any(alias.casefold() in normalized for alias in entity_aliases(entity_type, entity_id)):
                matched.add(tag)
    return frozenset(matched)


def _entity_gate(record: MemoryRecordV2, entity_tags: frozenset[str]) -> bool:
    """Tagged records need a positive match on every tag; untagged notes go by topic."""

    tags = frozenset(tag for tag in record.tags if tag.startswith("entity:"))
    if tags:
        return tags <= entity_tags
    return True


def _scope_gate(record: MemoryRecordV2, query: QueryContextV2) -> bool:
    spec = PREDICATES_BY_KEY.get(record.predicate)
    scoped = bool(spec and spec.scope_dimensions)
    if query.scope_state is QueryScopeState.UNKNOWN:
        return not scoped
    if query.scope_state is QueryScopeState.ABSENT:
        return record.scope_state is ScopeState.ABSENT
    if record.scope_state is ScopeState.ABSENT:
        return True
    return record.scope == (query.scope or {})


def _valid(record: MemoryRecordV2, now: datetime) -> bool:
    return record.enabled and not record.is_deleted and (record.expires_at is None or record.expires_at > now)


def _ranking(record: MemoryRecordV2, score: float) -> tuple[float, int, int, str]:
    return (-score, -int(record.priority == "REQUIRED"), -record.version, record.memory_key)


def _preference_lane(gated: list[MemoryRecordV2], limit: int) -> list[MemoryRecordV2]:
    """Preferences are selected without a query and without the score threshold."""

    preferences = [record for record in gated if record.memory_kind == "PREFERENCE"]
    preferences.sort(key=lambda record: _ranking(record, 1.0))
    return preferences[: max(0, limit)]


def select_v2(
    records: list[MemoryRecordV2],
    context: QueryContextV2,
    *,
    top_k: int = 5,
    min_score: float = 0.35,
    inject_inferred: bool = True,
    preference_items: int = 3,
) -> tuple[SelectedV2, ...]:
    """Preference lane plus scored facts/notes; the two are gated differently by design."""

    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    valid = [record for record in records if _valid(record, context.now)]
    if not inject_inferred:
        valid = [record for record in valid if record.author != "AGENT"]
    entity_tags = context.entity_tags or query_entity_tags(context.query, valid)
    gated = [record for record in valid if _entity_gate(record, entity_tags) and _scope_gate(record, context)]
    preferences = _preference_lane(gated, min(preference_items, top_k))
    remaining = max(0, top_k - len(preferences))
    selected = [SelectedV2(record, 1.0) for record in preferences]
    query = (context.query or "").strip()
    if query and remaining:
        scorable = [record for record in gated if record.memory_kind != "PREFERENCE"]
        scores = _score_records(scorable, query)
        matched = [
            SelectedV2(record, scores[record.memory_key])
            for record in scorable
            if record.memory_key in scores and scores[record.memory_key] >= min_score
        ]
        matched.sort(key=lambda item: _ranking(item.record, item.score))
        selected.extend(matched[:remaining])
    return tuple(selected)
