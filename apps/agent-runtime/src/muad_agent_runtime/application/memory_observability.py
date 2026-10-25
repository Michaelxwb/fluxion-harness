"""Bounded memory telemetry and content-free tool audit projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from muad_api import AppError
from muad_contracts.memory import FactContent, MemoryCandidate, MemoryCategory
from pydantic import JsonValue, ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from ..metrics import record_counter
from .memory_write import validate_memory_content

_OUTCOMES = frozenset(
    {
        "OK",
        "ERROR",
        "UNCHANGED",
        "STALE",
        "PROPOSED",
        "CONFIRMED",
        "REJECTED",
        "INVALID",
        "EXPIRED",
        "CLAIMED",
        "RETRY",
        "DONE",
        "SKIPPED",
        "FAILED",
        "FENCED",
        "DISABLED",
        "TIMEOUT",
        "QUEUE_FULL",
    }
)
_LABELS = {
    "memory_write_total": ("outcome", _OUTCOMES),
    "memory_confirmation_total": ("outcome", _OUTCOMES),
    "memory_consolidation_enqueue_total": ("outcome", _OUTCOMES),
    "memory_consolidation_total": ("outcome", _OUTCOMES),
    "memory_expiry_total": ("outcome", _OUTCOMES),
    "memory_quota_evictions_total": ("", frozenset()),
    "memory_write_conflicts_total": ("reason", frozenset({"VERSION", "QUOTA", "POLICY", "STALE", "ERROR"})),
}


def _checked(name: str, labels: Mapping[str, str] | None, amount: int) -> dict[str, str]:
    if name not in _LABELS or isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        raise ValueError("invalid memory metric")
    key, allowed = _LABELS[name]
    values = dict(labels or {})
    if set(values) != ({key} if key else set()) or (key and values[key] not in allowed):
        raise ValueError("invalid memory metric labels")
    return values


def observe(name: str, *, labels: Mapping[str, str] | None = None, amount: int = 1) -> None:
    record_counter(name, amount, _checked(name, labels, amount))


@dataclass(frozen=True, slots=True)
class _Sample:
    name: str
    labels: Mapping[str, str]
    amount: int


@dataclass(slots=True)
class _CommitObservations:
    pending: dict[SessionTransaction, list[_Sample]] = field(default_factory=dict)
    committed: set[SessionTransaction] = field(default_factory=set)

    def did_commit(self, session: Session) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is not None:
            self.committed.add(transaction)

    def ended(self, session: Session, transaction: SessionTransaction) -> None:
        samples = self.pending.pop(transaction, [])
        committed = transaction in self.committed
        self.committed.discard(transaction)
        if not committed:
            return
        if transaction.parent is not None:
            self.pending.setdefault(transaction.parent, []).extend(samples)
            return
        for sample in samples:
            observe(sample.name, labels=sample.labels, amount=sample.amount)


def after_commit(
    session: AsyncSession,
    name: str,
    *,
    labels: Mapping[str, str] | None = None,
    amount: int = 1,
) -> None:
    values = _checked(name, labels, amount)
    sync = session.sync_session
    transaction = sync.get_nested_transaction() or sync.get_transaction()
    if transaction is None:
        raise RuntimeError("memory metric requires a caller-owned transaction")
    state = sync.info.get("memory_observations")
    if not isinstance(state, _CommitObservations):
        state = _CommitObservations()
        sync.info["memory_observations"] = state
        event.listen(sync, "after_commit", state.did_commit)
        event.listen(sync, "after_transaction_end", state.ended)
    state.pending.setdefault(transaction, []).append(_Sample(name, values, amount))


def memory_audit_preview(
    arguments: Mapping[str, object],
    *,
    args_hash: str,
    status: str,
    canaries: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    preview: dict[str, JsonValue] = {"args_hash": args_hash, "status": status}
    key, category = arguments.get("memory_key"), arguments.get("category")
    if isinstance(key, str):
        try:
            candidate = MemoryCandidate(
                memory_key=key,
                category=MemoryCategory.PREFERENCE,
                content=FactContent(value="audit metadata"),
            )
            validate_memory_content(candidate, source_quote=None, credential_canaries=canaries)
        except (AppError, ValidationError):
            preview["memory_key"] = "<redacted>"
        else:
            preview["memory_key"] = key
    if category in ("PREFERENCE", "WORK_STYLE", "EXPLICIT"):
        preview["category"] = str(category)
    return preview
