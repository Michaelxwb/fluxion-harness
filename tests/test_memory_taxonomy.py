"""Controlled v2 taxonomy and entity alias tests (TASK-001)."""

from __future__ import annotations

from collections import Counter

import pytest
from muad_contracts.memory_taxonomy import (

    CORE_PREDICATES,
    ENTITY_TYPE_DISPLAY_NAMES,
    PREDICATES_BY_KEY,
    SCOPE_DIMENSIONS,
    entity_aliases,
    validate_scope,
    validate_tag,
)

pytestmark = pytest.mark.unit


def test_core_taxonomy_has_13_unique_predicates_with_expected_kinds():
    assert len(CORE_PREDICATES) == 13
    assert len(PREDICATES_BY_KEY) == 13
    assert Counter(item.kind for item in CORE_PREDICATES) == {
        "PREFERENCE": 9,
        "FACT": 3,
        "NOTE": 1,
    }
    assert {item.key for item in CORE_PREDICATES} >= {
        "report_format",
        "language",
        "timezone",
        "role",
        "team",
        "owned_system",
        "general",
    }


def test_taxonomy_scope_and_entity_aliases_are_closed_mappings():
    assert set(SCOPE_DIMENSIONS) == {"audience", "region"}
    assert "internal" in SCOPE_DIMENSIONS["audience"]
    assert ENTITY_TYPE_DISPLAY_NAMES == {"customer": "客户", "project": "项目"}
    assert entity_aliases("customer", "A") == ("A", "A 客户", "客户 A")
    assert entity_aliases("vendor", "A") == ()


def test_scope_and_tag_validation_reject_unrecognized_values():
    validate_scope("ABSENT", {})
    validate_scope("EXPLICIT", {"audience": "external"})
    with pytest.raises(ValueError):
        validate_scope("ABSENT", {"audience": "internal"})
    with pytest.raises(ValueError):
        validate_scope("EXPLICIT", {"audience": "partner"})
    with pytest.raises(ValueError):
        validate_scope("UNKNOWN", {})
    validate_tag("topic:data-security")
    validate_tag("entity:customer:A")
    with pytest.raises(ValueError):
        validate_tag("entity:customer:")
    with pytest.raises(ValueError):
        validate_tag("topic:")
