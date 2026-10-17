"""B-206: public event whitelist, snapshot parts, feed views and typed cursors."""

from datetime import UTC, datetime
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.unit
STAMP = datetime(2026, 10, 17, tzinfo=UTC)


def _module():
    return import_module("muad_contracts.webchat_events")


def _contract(name: str) -> type[BaseModel]:
    value: object = getattr(_module(), name)
    assert isinstance(value, type) and issubclass(value, BaseModel)
    return value


def _envelope(event_type: str, data: dict[str, object], **changes: object) -> dict[str, object]:
    return {"type": event_type, "event_id": str(uuid4()), "conversation_id": str(uuid4()),
            "run_id": str(uuid4()), "seq": 17, "timestamp": STAMP.isoformat(), "data": data, **changes}


def _notification(seq: int, **changes: object) -> dict[str, object]:
    return {"id": str(uuid4()), "seq": seq, "entry_id": str(uuid4()), "agent_id": str(uuid4()),
            "conversation_id": str(uuid4()), "run_id": str(uuid4()), "kind": "RUN_COMPLETED",
            "summary": "结果已就绪", "created_at": STAMP, **changes}


@pytest.mark.parametrize("event_type,data", [
    ("message.delta", {"reply_generation": 0, "text": "你好"}),
    ("message.snapshot", {"reply_generation": 1, "text": "replacement", "part_index": 0, "part_count": 1}),
    ("run.stage", {"stage": "RUNNING", "started_at": STAMP.isoformat()}),
    ("run.terminal", {"status": "COMPLETED"}),
    ("run.error", {"code": "MODEL_CAPACITY_UNAVAILABLE", "retryable": True}),
    ("confirmation.required", {"submission_id": str(uuid4()), "source_event_id": str(uuid4()),
                               "prompt": "请选择", "options": [{"value": "17", "label": "原始选项"}],
                               "expires_at": STAMP.isoformat()}),
    ("artifact.created", {"artifact_id": str(uuid4()), "kind": "DOCUMENT", "media_type": "application/pdf",
                          "size": 10, "filename": "报告.pdf", "checksum": "sha256:" + "a" * 64,
                          "available": True}),
])
def test_b206_whitelisted_events_parse_to_typed_public_payloads_and_round_trip(
    event_type: str, data: dict[str, object],
) -> None:
    wire = _envelope(event_type, data)
    parsed = _module().parse_public_event(wire)
    assert parsed is not None and parsed.type == event_type and parsed.seq == 17
    assert str(parsed.run_id) == wire["run_id"] and str(parsed.event_id) == wire["event_id"]
    assert isinstance(parsed.data, BaseModel)
    assert _module().decode_public_event_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("event_type", [
    "model.started", "model.completed", "tool.started", "tool.completed", "tool.result",
    "SYSTEM", "reasoning.delta", "memory.proposal", "future.private.event", "",
])
def test_b206_unknown_or_private_events_are_not_exposed_even_with_private_data(event_type: str) -> None:
    assert _module().parse_public_event(_envelope(event_type, {"api_key": "private-canary"})) is None


@pytest.mark.parametrize("field", ["api_key", "storage_key", "tool_arguments", "reasoning", "model_request"])
def test_b206_known_event_payloads_reject_extra_private_fields(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _module().parse_public_event(_envelope("message.delta", {
            "reply_generation": 0, "text": "public", field: "private-canary",
        }))


def test_b206_known_malformed_frames_fail_explicitly_rather_than_looking_like_filtering() -> None:
    with pytest.raises(ValidationError, match="reply_generation"):
        _module().parse_public_event(_envelope("message.delta", {"text": "missing generation"}))
    with pytest.raises(ValidationError, match="seq"):
        _module().parse_public_event(_envelope(
            "message.delta", {"reply_generation": 0, "text": "ok"}, seq=True,
        ))
    with pytest.raises(ValueError, match="JSON object"):
        _module().decode_public_event_json("[]")
    with pytest.raises(ValueError):
        _module().decode_public_event_json("not-json")


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_b206_non_json_values_are_rejected_even_in_an_unknown_event(literal: str) -> None:
    with pytest.raises(ValueError):
        _module().decode_public_event_json('{"type":"private.future","data":{"number":' + literal + '}}')
    with pytest.raises(ValueError):
        _module().parse_public_event({"type": "private.future", "data": {"number": float(literal)}})


def test_b206_snapshot_parts_share_persistent_identity_and_preserve_unicode_text() -> None:
    data = {"reply_generation": 2, "text": "正文", "part_index": 0, "part_count": 3}
    identity = _envelope("message.snapshot", data)
    texts = ["正文", "🌏\n", "最后"]
    parts = [_module().parse_public_event({**identity, "data": {**data, "text": text, "part_index": index}})
             for index, text in enumerate(texts)]
    assert len({(part.run_id, part.event_id, part.seq, part.data.reply_generation) for part in parts}) == 1
    assert [part.data.part_index for part in parts] == [0, 1, 2]
    assert "".join(part.data.text for part in parts) == "正文🌏\n最后"
    assert all(part.data.part_count == 3 for part in parts)
    # Part progress is not another canonical event and not a client-applied cursor.
    assert all("applied_seq" not in part.model_dump() for part in parts)


@pytest.mark.parametrize("changes", [
    {"part_index": -1}, {"part_count": 0}, {"part_index": 2, "part_count": 2},
    {"part_count": True}, {"part_index": 1.0}, {"reply_generation": -1}, {"reply_generation": "1"},
])
def test_b206_snapshot_part_coordinates_are_strict_and_bounded(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _module().parse_public_event(_envelope("message.snapshot", {
            "reply_generation": 0, "text": "snapshot", "part_index": 0, "part_count": 1, **changes,
        }))


def test_b206_checkpoint_uses_a_scanned_watermark_without_inventing_a_canonical_event() -> None:
    event = _module().parse_public_event({
        "type": "checkpoint", "conversation_id": str(uuid4()), "run_id": str(uuid4()),
        "seq": 0, "timestamp": STAMP.isoformat(), "data": {},
    })
    assert event.type == "checkpoint" and event.seq == 0
    assert "event_id" not in event.model_dump()
    assert _module().decode_public_event_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError, match="text"):
        _module().parse_public_event({**event.model_dump(mode="json"), "data": {"text": "cannot append"}})
    assert _module().parse_public_event({"type": "heartbeat", "seq": 999, "data": {}}) is None


def test_b206_public_errors_accept_codes_but_no_arbitrary_provider_text() -> None:
    safe = _contract("WebchatSafeError")(code="MODEL_CAPACITY_UNAVAILABLE", retryable=True)
    assert safe.model_dump() == {"code": "MODEL_CAPACITY_UNAVAILABLE", "retryable": True}
    with pytest.raises(ValidationError, match="message"):
        _contract("WebchatSafeError")(code="MODEL_FAILED", retryable=False, message="provider secret canary")
    with pytest.raises(ValidationError, match="code"):
        _contract("WebchatSafeError")(code="bad key: private-canary", retryable=False)
    with pytest.raises(ValidationError, match="status"):
        _module().parse_public_event(_envelope("run.terminal", {"status": "RUNNING"}))


def test_b206_cursor_errors_distinguish_retention_ahead_and_another_run() -> None:
    run_id = uuid4()
    window = _contract("WebchatCursorWindow")(run_id=run_id, minimum_after_seq=5, last_seq=50)
    check = _module().validate_run_cursor
    for after_seq in (5, 17, 49, 50):
        assert check(_contract("WebchatRunCursor")(run_id=run_id, after_seq=after_seq), window) is None
    for after_seq, code in ((0, "CURSOR_EXPIRED"), (4, "CURSOR_EXPIRED"), (51, "CURSOR_AHEAD")):
        error = check(_contract("WebchatRunCursor")(run_id=run_id, after_seq=after_seq), window)
        assert error.code == code and error.requested_seq == after_seq and error.last_seq == 50
        assert error.minimum_after_seq == 5
    wrong = check(_contract("WebchatRunCursor")(run_id=uuid4(), after_seq=17), window)
    assert wrong.code == "CURSOR_RUN_MISMATCH" and wrong.run_id == run_id
    with pytest.raises(ValidationError, match="minimum_after_seq"):
        _contract("WebchatCursorWindow")(run_id=run_id, minimum_after_seq=51, last_seq=50)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", 1 << 63])
def test_b206_cursor_counts_are_strict_and_fit_persisted_sequences(value: object) -> None:
    with pytest.raises(ValidationError, match="after_seq"):
        _contract("WebchatRunCursor")(run_id=uuid4(), after_seq=value)


def test_b206_notifications_have_public_refs_independent_read_state_and_a_monotonic_feed() -> None:
    notifications = [_notification(3), _notification(9, read_at=STAMP)]
    page = _contract("WebchatNotificationPage")(
        items=notifications, page=1, page_size=50, total=2, through_seq=12, next_seq=12, unread_count=1,
    )
    assert [item.seq for item in page.items] == [3, 9] and page.next_seq == 12
    assert page.items[0].read_at is None and page.items[1].read_at == STAMP
    assert page.unread_count == 1
    assert "delivery_state" not in page.items[0].model_dump()  # Read is independent of outbound delivery.
    empty = _contract("WebchatNotificationPage")(
        items=[], page=1, page_size=50, total=0, through_seq=500, next_seq=500, unread_count=0,
    )
    assert empty.next_seq == 500  # Cleanup cannot imply resetting the stable feed to zero.
    task = _contract("WebchatNotification")(**_notification(10, kind="TASK_RESULT", task_id=str(uuid4())))
    assert task.task_id is not None
    waiting = _contract("WebchatNotification")(**_notification(11, kind="RUN_WAITING_INPUT"))
    assert waiting.kind == "RUN_WAITING_INPUT"


@pytest.mark.parametrize("field", ["storage_key", "delivery_key", "lease_owner", "token", "raw_result"])
def test_b206_notifications_cannot_expose_delivery_internals_or_private_payloads(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _contract("WebchatNotification")(**_notification(1, **{field: "private-canary"}))


def test_b206_notification_page_rejects_ambiguous_order_or_cursor_and_task_without_source() -> None:
    for changes in ({"items": [_notification(9), _notification(3)]}, {"next_seq": 2}, {"next_seq": 13}):
        with pytest.raises(ValidationError):
            _contract("WebchatNotificationPage")(**{
                "items": [_notification(3), _notification(9)], "page": 1, "page_size": 50,
                "total": 2, "through_seq": 12, "next_seq": 12, "unread_count": 1, **changes,
            })
    with pytest.raises(ValidationError, match="task_id"):
        _contract("WebchatNotification")(**_notification(1, kind="TASK_RESULT"))


@pytest.mark.parametrize("event_type", [
    "message.delta", "message.snapshot", "run.stage", "run.terminal",
    "run.error", "confirmation.required", "artifact.created", "checkpoint",
])
def test_b206_every_public_payload_forbids_additional_fields(event_type: str) -> None:
    with pytest.raises(ValidationError, match="private_canary") as error:
        _module().parse_public_event(_envelope(event_type, {"private_canary": "secret-canary-value"}))
    assert "secret-canary-value" not in str(error.value)


def test_b206_nonstring_discriminators_and_non_json_runtime_objects_are_handled_explicitly() -> None:
    assert _module().parse_public_event({"type": [], "data": {}}) is None
    assert _module().parse_public_event({"data": {}}) is None
    with pytest.raises(ValueError, match="datetime"):
        _module().parse_public_event(_envelope("message.delta", {"reply_generation": 0, "text": "ok"},
                                              timestamp=STAMP))
    with pytest.raises(ValidationError, match="timestamp"):
        _module().parse_public_event(_envelope("message.delta", {"reply_generation": 0, "text": "ok"},
                                              timestamp="2026-10-17T00:00:00"))
