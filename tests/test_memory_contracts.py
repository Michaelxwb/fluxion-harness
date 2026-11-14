"""User memory v2 contract boundary tests (TASK-001)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from muad_contracts.memory import (
    ForgetMemoryInput,
    MemoryCandidate,
    MemoryItem,
    MemoryResolution,
    RecallMemoryInput,
    RememberMemoryInput,
    ScopeState,
)

pytestmark = pytest.mark.unit


def _preference(**overrides):
    payload = {
        "memory_kind": "PREFERENCE",
        "predicate": "report_format",
        "scope_state": "EXPLICIT",
        "scope": {"audience": "internal"},
        "content": {"format": "PDF"},
        "title": "内部报告默认 PDF",
        "summary": "用户明确要求内部报告默认使用 PDF",
        "tags": ["topic:报告格式", "topic:PDF"],
    }
    payload.update(overrides)
    return payload


def test_v2_input_normalizes_taxonomy_owned_identity_fields():
    candidate = RememberMemoryInput.model_validate(_preference()).normalized(taxonomy_version=3)
    assert isinstance(candidate, MemoryCandidate)
    assert candidate.cardinality == "SINGLE"
    assert candidate.priority == "REQUIRED"
    assert candidate.taxonomy_version == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"predicate": "unknown"},
        {"memory_kind": "NOTE"},
        {"scope_state": "ABSENT", "scope": {"audience": "internal"}},
        {"scope_state": "EXPLICIT", "scope": {"audience": "partners"}},
        {"scope_state": "UNKNOWN"},
        {"tags": ["entity:vendor:ACME"]},
        {"tags": ["entity:customer:" + "x" * 65]},
        {"tags": ["topic:"]},
    ],
)
def test_v2_input_rejects_invalid_taxonomy_or_scope(overrides):
    with pytest.raises(ValueError):
        RememberMemoryInput.model_validate(_preference(**overrides))


def test_v2_input_rejects_unknown_fields_and_empty_content():
    with pytest.raises(ValueError):
        RememberMemoryInput.model_validate({**_preference(), "memory_key": "client.key"})
    with pytest.raises(ValueError):
        RememberMemoryInput.model_validate(_preference(content={}))


def test_recall_and_forget_use_memory_id_without_compat_key():
    memory_id = uuid4()
    recall = RecallMemoryInput.model_validate({"memory_id": memory_id, "limit": 5})
    assert recall.memory_id == memory_id
    forget = ForgetMemoryInput.model_validate({"memory_id": memory_id, "expected_version": 2})
    assert forget.memory_id == memory_id
    with pytest.raises(ValueError):
        ForgetMemoryInput.model_validate({"memory_key": "v1.slot.fact.role.x", "expected_version": 2})


def test_resolution_carries_clarification_reason_not_a_boolean():
    item = MemoryItem(
        id=uuid4(),
        memory_key="v1.slot.preference.report_format.a1b2c3d4e5f60718",
        memory_kind="PREFERENCE",
        predicate="report_format",
        scope_state=ScopeState.EXPLICIT,
        scope={"audience": "internal"},
        content={"format": "PDF"},
        title="内部报告默认 PDF",
        summary="",
        tags=[],
        cardinality="SINGLE",
        priority="REQUIRED",
        author="USER",
        authority="USER_EXPLICIT",
        version=1,
        enabled=True,
        expires_at=None,
        last_confirmed_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    resolution = MemoryResolution(
        status="empty",
        entries=(item,),
        cache_status="miss",
        cache_revision=3,
        source_revision=3,
        truncated=False,
        error_code=None,
        clarification_reason="SCOPE_UNKNOWN",
        retrieval_trace_id="trace-1",
    )
    assert resolution.clarification_reason == "SCOPE_UNKNOWN"
    assert resolution.entries[0].id == item.id
