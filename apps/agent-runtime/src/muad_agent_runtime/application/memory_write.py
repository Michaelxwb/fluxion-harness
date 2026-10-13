"""Pure write policies. Transaction ownership belongs to the command service."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    MEMORY_MAX_BYTES,
    MEMORY_MAX_RECORDS,
    FactContent,
    MemoryAuthor,
    MemoryCandidate,
    MemoryCategory,
    MemoryRetirementReason,
    MemorySourceEvidence,
    TaskStateContent,
    memory_content_bytes,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from pydantic import ValidationError

_SECRET = re.compile(
    r"sk-[a-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|gh[pousr]_[a-z0-9]{20,}"
    r"|xox[baprs]-[a-z0-9-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|(?:password|passwd|secret|api[_ -]?key|(?:access[_ -]?)?token)[\"']?\s*[:=]\s*\S+",
    re.I,
)
_SECRET_KEY = re.compile(
    r"(?:^|[.:-])(?:password|passwd|secret|token|api[._ -]?key|access[._ -]?token|credential)(?:$|[.:-])",
    re.I,
)
_USER_TOMBSTONES = frozenset({MemoryRetirementReason.USER_DELETE, MemoryRetirementReason.ADMIN_DELETE})
_AUTOMATIC_RETIREMENTS = frozenset(
    {MemoryRetirementReason.EXPIRE, MemoryRetirementReason.QUOTA_EVICT, MemoryRetirementReason.TASK_COMPLETE}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryWriteState:
    memory_key: str
    category: MemoryCategory
    content: FactContent | TaskStateContent
    author: MemoryAuthor
    version: int
    updated_at: datetime
    source_quote: str | None = None
    expires_at: datetime | None = None
    confidence: int = 2
    confirmed_count: int = 0
    enabled: bool = True
    is_deleted: bool = False
    retirement_reason: MemoryRetirementReason | None = None
    retirement_revision: int | None = None
    last_source_run_id: UUID | None = None
    last_source_seq: int | None = None
    source_time: datetime | None = None

    def __post_init__(self) -> None:
        for stamp in (self.updated_at, self.expires_at, self.source_time):
            if stamp is not None and stamp.utcoffset() is None:
                raise ValueError("memory timestamps must be timezone aware")
        if self.version < 1 or not 1 <= self.confidence <= 5 or self.confirmed_count < 0:
            raise ValueError("invalid memory counters")
        if not isinstance(self.author, MemoryAuthor) or not isinstance(self.category, MemoryCategory):
            raise TypeError("memory author/category must be strict enums")


@dataclass(frozen=True, slots=True)
class MemoryCapacityPlan:
    expired_keys: tuple[str, ...]
    evicted_keys: tuple[str, ...]


AutomaticReason = Literal[
    "APPLY",
    "CONFIRMED_PROTECTED",
    "DISABLED",
    "USER_TOMBSTONE",
    "VERSION_CHANGED",
    "SOURCE_MISSING",
    "OLD_SOURCE",
    "RETIREMENT_BARRIER",
]


@dataclass(frozen=True, slots=True)
class MemoryAutomaticDecision:
    allowed: bool
    reason: AutomaticReason


def _candidate_strings(candidate: MemoryCandidate) -> tuple[str, ...]:
    content = candidate.content
    if isinstance(content, FactContent):
        return candidate.memory_key, content.value
    return (
        candidate.memory_key,
        content.title,
        content.goal,
        content.next_action,
        *content.constraints,
        *content.open_items,
    )


def validate_memory_content(
    candidate: MemoryCandidate,
    *,
    source_quote: str | None,
    credential_canaries: tuple[str, ...],
) -> None:
    try:
        candidate = MemoryCandidate.model_validate(candidate.model_dump(mode="json"))
    except ValidationError:
        raise AppError(ErrorCode.MEMORY_VALUE_INVALID) from None
    if source_quote is not None and (not source_quote.strip() or len(source_quote) > 512):
        raise AppError(ErrorCode.MEMORY_VALUE_INVALID)
    values = _candidate_strings(candidate) + ((source_quote,) if source_quote is not None else ())
    values = tuple(unicodedata.normalize("NFKC", value) for value in values)
    canaries = tuple(unicodedata.normalize("NFKC", value) for value in credential_canaries if value)
    if _SECRET_KEY.search(candidate.memory_key) or any(
        _SECRET.search(value) or any(canary in value for canary in canaries) for value in values
    ):
        raise AppError(ErrorCode.MEMORY_FORBIDDEN_CONTENT)


def fact_bytes(state: MemoryWriteState) -> int:
    return memory_content_bytes(state.content.model_dump(mode="json"), state.source_quote)


def _active(state: MemoryWriteState, now: datetime) -> bool:
    return not state.is_deleted and (state.expires_at is None or state.expires_at > now)


def _capacity(states: Iterable[MemoryWriteState]) -> tuple[int, int]:
    rows = tuple(states)
    return len(rows), sum(fact_bytes(row) for row in rows)


def _over_capacity(capacity: tuple[int, int], limits: tuple[int, int]) -> bool:
    return any(value > limit for value, limit in zip(capacity, limits, strict=True))


def _eviction_order(state: MemoryWriteState) -> tuple[int, int, datetime, str]:
    return state.confidence, state.confirmed_count, state.source_time or state.updated_at, state.memory_key


def plan_memory_capacity(
    current: Iterable[MemoryWriteState],
    proposed: Iterable[MemoryWriteState],
    *,
    now: datetime,
    settings: MemoryPolicySettings,
) -> MemoryCapacityPlan:
    if now.utcoffset() is None:
        raise ValueError("memory timestamps must be timezone aware")
    rows, writes = tuple(current), tuple(proposed)
    if len({row.memory_key for row in writes}) != len(writes):
        raise AppError(ErrorCode.MEMORY_KEY_INVALID)
    live = {row.memory_key: row for row in rows if _active(row, now)}
    before = _capacity(live.values())
    expired = tuple(sorted(row.memory_key for row in rows if not row.is_deleted and not _active(row, now)))
    final = {**live, **{row.memory_key: row for row in writes}}
    final = {key: row for key, row in final.items() if _active(row, now)}
    after = _capacity(final.values())
    limits = settings.max_entries_per_user, settings.max_memory_bytes_per_user
    growth = any(new > old for new, old in zip(after, before, strict=True))
    if _over_capacity(before, limits):
        if growth:
            raise AppError(ErrorCode.MEMORY_QUOTA_EXCEEDED)
        return MemoryCapacityPlan(expired, ())
    return _evict_to_fit(final, writes, after, limits, expired)


def _evict_to_fit(
    final: dict[str, MemoryWriteState],
    writes: tuple[MemoryWriteState, ...],
    capacity: tuple[int, int],
    limits: tuple[int, int],
    expired: tuple[str, ...],
) -> MemoryCapacityPlan:
    protected = {row.memory_key for row in writes}
    candidates = sorted(
        (
            row
            for row in final.values()
            if row.author is MemoryAuthor.AGENT and row.memory_key not in protected
        ),
        key=_eviction_order,
    )
    evicted: list[str] = []
    for row in candidates:
        if not _over_capacity(capacity, limits):
            break
        evicted.append(row.memory_key)
        capacity = capacity[0] - 1, capacity[1] - fact_bytes(row)
    if _over_capacity(capacity, limits) or _over_capacity(capacity, (MEMORY_MAX_RECORDS, MEMORY_MAX_BYTES)):
        raise AppError(ErrorCode.MEMORY_QUOTA_EXCEEDED)
    return MemoryCapacityPlan(expired, tuple(evicted))


def decide_automatic_update(
    stored: MemoryWriteState | None,
    *,
    expected_version: int | None,
    evidence: MemorySourceEvidence | None,
    source_run_id: UUID,
    same_conversation: bool,
) -> MemoryAutomaticDecision:
    reason = _automatic_rejection(stored, expected_version, evidence, source_run_id, same_conversation)
    return MemoryAutomaticDecision(reason is None, reason or "APPLY")


def _automatic_rejection(
    stored: MemoryWriteState | None,
    expected_version: int | None,
    evidence: MemorySourceEvidence | None,
    source_run_id: UUID,
    same_conversation: bool,
) -> AutomaticReason | None:
    if stored is not None:
        if stored.is_deleted and stored.retirement_reason in _USER_TOMBSTONES:
            return "USER_TOMBSTONE"
        if stored.author is not MemoryAuthor.AGENT:
            return "CONFIRMED_PROTECTED"
        if not stored.enabled:
            return "DISABLED"
        if expected_version != stored.version:
            return "VERSION_CHANGED"
    if evidence is None:
        return "SOURCE_MISSING"
    if stored is None:
        return "VERSION_CHANGED" if expected_version is not None else None
    if stored.is_deleted and (
        stored.retirement_reason not in _AUTOMATIC_RETIREMENTS
        or stored.retirement_revision is None
        or evidence.source_scope_revision < stored.retirement_revision
    ):
        return "RETIREMENT_BARRIER"
    if (same_conversation or stored.last_source_run_id == source_run_id) and (
        stored.last_source_seq is not None and evidence.source_seq <= stored.last_source_seq
    ):
        return "OLD_SOURCE"
    return None


def check_manual_update(
    stored: MemoryWriteState | None,
    *,
    expected_version: int | None,
    restore: bool,
    enable: bool,
) -> None:
    if expected_version != (stored.version if stored is not None else None):
        raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
    if stored is not None and ((stored.is_deleted and not restore) or (not stored.enabled and not enable)):
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)


def memory_expiry(
    author: MemoryAuthor,
    category: MemoryCategory,
    now: datetime,
    settings: MemoryPolicySettings,
) -> datetime | None:
    if now.utcoffset() is None:
        raise ValueError("memory timestamps must be timezone aware")
    if category is MemoryCategory.TASK_STATE:
        days = settings.ttl_days_task_state
    else:
        days = {
            MemoryAuthor.USER: settings.ttl_days_user,
            MemoryAuthor.ADMIN: settings.ttl_days_user,
            MemoryAuthor.AGENT: settings.ttl_days_agent,
        }[author]
    return now + timedelta(days=days) if days else None
