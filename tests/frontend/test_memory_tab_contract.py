"""TASK-007: v2 frontend wire contract is strict and separate from legacy types."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TYPES = ROOT / "apps/console-platform/frontend/src/modules/user-identity/memoryTypes.ts"


def _source() -> str:
    assert TYPES.exists(), f"missing types: {TYPES}"
    return TYPES.read_text(encoding="utf-8")


def test_v2_types_declare_kind_scope_and_stable_identity_fields():
    source = _source()
    assert "export type MemoryKind = 'PREFERENCE' | 'FACT' | 'NOTE';" in source
    assert "export type MemoryScopeState = 'ABSENT' | 'EXPLICIT';" in source
    for field in (
        "id: string;",
        "memory_key: string;",
        "memory_kind: MemoryKind;",
        "scope_state: MemoryScopeState;",
        "content: Record<string, unknown>;",
        "cardinality: MemoryCardinality;",
        "priority: MemoryPriority;",
        "status: MemoryStatus;",
    ):
        assert field in source


def test_v2_types_do_not_reintroduce_any_or_legacy_category():
    source = re.sub(r"/\*[\s\S]*?\*/", "", _source())
    assert "category:" not in source
    assert not re.search(r"(?<![\w.])any(?![\w])", source)
