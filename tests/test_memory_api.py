"""TASK-006: v2 admin transport models freeze the public shape."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from muad_contracts.memory_admin import MemoryAdminItem, MemoryCommandRequest


def test_v2_admin_item_carries_structured_fields_and_derived_status():
    now = datetime(2026, 11, 14, tzinfo=UTC)
    item = MemoryAdminItem(
        id=uuid4(),
        memory_key="v1.slot.preference.report_format.x",
        memory_kind="PREFERENCE",
        predicate="report_format",
        scope_state="ABSENT",
        scope={},
        content={"format": "PDF"},
        title="报告",
        summary="",
        tags=["topic:报告"],
        cardinality="SINGLE",
        priority="REQUIRED",
        author="USER",
        authority="USER_EXPLICIT",
        version=1,
        enabled=True,
        expires_at=None,
        last_confirmed_at=None,
        status="ACTIVE",
        create_time=now,
        update_time=now,
    )
    assert item.memory_kind == "PREFERENCE" and item.status == "ACTIVE"


def test_v2_command_request_rejects_unknown_fields():
    request = MemoryCommandRequest(
        actor_id=uuid4(),
        action="DELETE",
        memory_id=uuid4(),
        expected_version=1,
    )
    assert request.action == "DELETE"
    try:
        MemoryCommandRequest.model_validate({"actor_id": uuid4(), "action": "DELETE", "legacy_key": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("legacy_key must be rejected")
