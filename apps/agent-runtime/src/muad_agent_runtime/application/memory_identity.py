"""Stable user-memory v2 identities; identity is never supplied by a caller."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from uuid import UUID

from muad_contracts.canonical import canonical_json
from muad_contracts.memory import MemoryCandidate


def _sha16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def scope_hash(scope: dict[str, str]) -> str:
    return _sha16(canonical_json(scope))


def member_hash(content: Mapping[str, object]) -> str:
    return _sha16(canonical_json(content))


def single_key(candidate: MemoryCandidate) -> str:
    if candidate.cardinality != "SINGLE":
        raise ValueError("single_key requires SINGLE cardinality")
    return f"v1.slot.{candidate.memory_kind.lower()}.{candidate.predicate}.{scope_hash(candidate.scope)}"


def set_key(candidate: MemoryCandidate) -> str:
    if candidate.cardinality != "SET":
        raise ValueError("set_key requires SET cardinality")
    return (
        f"v1.set.{candidate.memory_kind.lower()}.{candidate.predicate}."
        f"{scope_hash(candidate.scope)}.{member_hash(candidate.content)}"
    )


def note_key(note_id: UUID) -> str:
    return f"v1.note.{note_id}"


def memory_key(candidate: MemoryCandidate, *, note_id: UUID | None = None) -> str:
    if candidate.memory_kind == "NOTE":
        if note_id is None:
            raise ValueError("NOTE requires a stable UUID")
        return note_key(note_id)
    return single_key(candidate) if candidate.cardinality == "SINGLE" else set_key(candidate)
