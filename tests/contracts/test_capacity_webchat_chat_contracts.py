"""B-205: browser-safe Webchat contracts, without database or network substitutes."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from muad_contracts.platform_settings import WebchatSettings
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.unit
ISSUED = datetime(2026, 10, 17, tzinfo=UTC)


def _contract(name: str) -> type[BaseModel]:
    value: object = getattr(import_module("muad_contracts.webchat"), name)
    assert isinstance(value, type) and issubclass(value, BaseModel)
    return value


def _message(**changes: object) -> dict[str, object]:
    return {"conversation_id": str(uuid4()), "message_id": str(uuid4()), "text": "你好", **changes}


def _metadata(**changes: object) -> dict[str, object]:
    return {"configured": True, "status": "ACTIVE", "token_id": str(uuid4()), "revision": 1,
            "issued_at": ISSUED, "expires_at": ISSUED + timedelta(days=30), **changes}


def _attachment(**changes: object) -> dict[str, object]:
    return {"kind": "DOCUMENT", "media_type": "application/pdf", "size": 4096,
            "filename": "报告.pdf", "checksum": "sha256:" + "a" * 64, **changes}


def _history(seq: int, **changes: object) -> dict[str, object]:
    return {"event_id": str(uuid4()), "seq": seq, "run_id": str(uuid4()), "role": "USER",
            "text": "question", "attachments": [], "created_at": ISSUED, **changes}


def test_b205_exchange_accepts_only_a_secret_and_hides_it_in_diagnostics() -> None:
    request = _contract("WebchatTokenExchange")(token="webchat-token-canary")
    assert request.token.get_secret_value() == "webchat-token-canary"
    assert "webchat-token-canary" not in repr(request)
    assert "token" not in request.model_dump(mode="json")
    with pytest.raises(ValidationError) as error:
        _contract("WebchatTokenExchange")(token="webchat-token-canary", user_id=uuid4())
    assert "webchat-token-canary" not in str(error.value)


@pytest.mark.parametrize("name,payload", [
    ("WebchatTokenExchange", {"token": "canary"}),
    ("WebchatMessageRequest", _message()),
    ("WebchatConversationCreate", {}),
    ("WebchatResumeRequest", {"submission_id": str(uuid4()), "source_event_id": str(uuid4()),
                              "message_id": str(uuid4()), "selection": "keep-original-option"}),
])
@pytest.mark.parametrize("field", ["user_id", "platform_user_id", "tenant_id", "agent_id", "context"])
def test_b205_browser_requests_cannot_self_assert_identity_or_context(
    name: str, payload: dict[str, object], field: str,
) -> None:
    with pytest.raises(ValidationError, match=field):
        _contract(name)(**{**payload, field: "untrusted"})


@pytest.mark.parametrize("token", ["", "   ", 123, None])
def test_b205_empty_or_nonstring_tokens_are_invalid(token: object) -> None:
    with pytest.raises(ValidationError, match="token"):
        _contract("WebchatTokenExchange")(token=token)


def test_b205_token_metadata_and_one_time_issuance_have_distinct_serialization() -> None:
    metadata = _contract("WebchatTokenMetadata")(**_metadata())
    assert set(metadata.model_dump()) == {"configured", "status", "token_id", "revision",
                                          "issued_at", "expires_at"}
    issued = _contract("WebchatTokenIssued")(**_metadata(), token="one-time-token-canary")
    assert "token" not in issued.model_dump(mode="json")
    assert "one-time-token-canary" not in repr(issued)
    wire = issued.to_issuance_payload()
    assert wire["token"] == "one-time-token-canary" and wire["revision"] == 1
    with pytest.raises(ValidationError, match="token"):
        _contract("WebchatTokenMetadata")(**_metadata(), token="cannot-read-back")
    empty = _contract("WebchatTokenMetadata")(
        configured=False, status="NOT_CONFIGURED", token_id=None, revision=0, issued_at=None, expires_at=None,
    )
    assert empty.revision == 0
    assert _contract("WebchatTokenMutation")(expected_revision=0).expected_revision == 0


@pytest.mark.parametrize("changes", [
    {"expires_at": ISSUED}, {"issued_at": ISSUED.replace(tzinfo=None)}, {"revision": 0},
    {"token_id": None}, {"configured": False}, {"status": "NOT_CONFIGURED"},
])
def test_b205_token_metadata_must_be_complete_and_time_ordered(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _contract("WebchatTokenMetadata")(**_metadata(**changes))


def test_b205_verified_principal_and_public_session_do_not_carry_the_cookie_secret() -> None:
    session_id, user_id = uuid4(), uuid4()
    principal = _contract("ChatPrincipal")(
        tenant_id="tenant-a", platform_user_id=user_id, session_id=session_id, auth_method="USER_TOKEN",
    )
    assert principal.platform_user_id == user_id
    session = _contract("WebchatSessionInfo")(
        tenant_id="tenant-a", user_id=user_id, display_name="用户", session_id=session_id,
        expires_at=ISSUED + timedelta(hours=12), auth_context_id=session_id, auth_method="USER_TOKEN",
    )
    wire = session.model_dump(mode="json")
    assert wire["user_id"] == str(user_id) and wire["auth_context_id"] == str(session_id)
    assert "token" not in wire and "platform_user_id" not in wire
    with pytest.raises(ValidationError, match="auth_context_id"):
        _contract("WebchatSessionInfo")(**{**wire, "auth_context_id": str(uuid4())})


def test_b205_messages_always_fix_the_conversation_and_do_not_upload_model_history() -> None:
    payload = _message()
    request = _contract("WebchatMessageRequest")(**payload)
    assert str(request.conversation_id) == payload["conversation_id"] and request.text == "你好"
    assert request.upload_ids == []
    del payload["conversation_id"]
    with pytest.raises(ValidationError, match="conversation_id"):
        _contract("WebchatMessageRequest")(**payload)
    with pytest.raises(ValidationError):
        _contract("WebchatMessageRequest")(**_message(text="   "))
    upload_id = uuid4()
    attached = _contract("WebchatMessageRequest")(**_message(text="", upload_ids=[upload_id]))
    assert attached.text == "" and attached.upload_ids == [upload_id]
    with pytest.raises(ValidationError, match="upload_ids"):
        _contract("WebchatMessageRequest")(**_message(upload_ids=[upload_id, upload_id]))


def test_b205_message_limits_remain_live_policy_rather_than_dto_defaults() -> None:
    text = "界" * 30000  # UTF-8 exceeds the initial soft limit, but fits the startup hard limit.
    request = _contract("WebchatMessageRequest")(**_message(text=text))
    assert request.text == text
    # New acceptance applies the effective settings; replay must not reapply a changed soft limit.
    assert len(request.text.encode("utf-8")) > WebchatSettings().text_max_bytes


def test_b205_upload_and_artifact_views_have_separate_identities_and_no_storage_paths() -> None:
    upload = _contract("WebchatUpload")(**_attachment(), upload_id=uuid4(), expires_at=ISSUED)
    artifact = _contract("WebchatAttachment")(**_attachment(), artifact_id=uuid4(), available=True)
    assert "artifact_id" not in upload.model_dump() and "upload_id" not in artifact.model_dump()
    assert "storage_key" not in upload.model_dump() and "storage_key" not in artifact.model_dump()
    missing = _contract("WebchatAttachment")(**_attachment(), artifact_id=uuid4(), available=False)
    assert missing.available is False


@pytest.mark.parametrize("field", ["storage_key", "download_url", "token", "artifact_id"])
def test_b205_staging_upload_cannot_leak_internal_locations_or_fabricate_artifacts(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _contract("WebchatUpload")(**{
            **_attachment(), "upload_id": str(uuid4()), "expires_at": ISSUED, field: "private",
        })


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.0, float("nan"), float("inf")])
def test_b205_attachment_size_is_a_positive_strict_integer(value: object) -> None:
    with pytest.raises(ValidationError, match="size"):
        _contract("WebchatUpload")(**_attachment(size=value), upload_id=uuid4(), expires_at=ISSUED)


def test_b205_history_pages_allow_filtered_seq_gaps_but_fix_the_watermark_and_order() -> None:
    page = _contract("WebchatMessagePage")(
        items=[_history(3), _history(9, role="ASSISTANT")], page=1, page_size=50, total=400, through_seq=9,
    )
    assert page.total == 400 and [item.seq for item in page.items] == [3, 9]
    assert page.through_seq == 9
    for items in ([_history(10)], [_history(9), _history(3)], [_history(3), _history(3)]):
        with pytest.raises(ValidationError):
            _contract("WebchatMessagePage")(items=items, page=1, page_size=50, total=400, through_seq=9)
    with pytest.raises(ValidationError, match="through_seq"):
        _contract("WebchatHistoryQuery")(page=2)
    assert _contract("WebchatHistoryQuery")(page=2, through_seq=9).through_seq == 9


@pytest.mark.parametrize("role", ["SYSTEM", "TOOL", "REASONING"])
def test_b205_history_cannot_expose_private_model_or_tool_messages(role: str) -> None:
    with pytest.raises(ValidationError, match="role"):
        _contract("WebchatHistoryMessage")(**_history(1, role=role))
    with pytest.raises(ValidationError, match="tool_arguments"):
        _contract("WebchatHistoryMessage")(**_history(1, tool_arguments={"secret": "private"}))


def test_b205_pagination_uses_current_settings_and_standard_list_envelopes() -> None:
    settings = WebchatSettings(history_page_size=75)
    query = _contract("WebchatPageQuery")()
    assert query.page == 1 and query.effective_page_size(settings) == 75
    assert _contract("WebchatPageQuery")(page_size=100).effective_page_size(settings) == 100
    entry = _contract("WebchatEntry")(entry_id=uuid4(), agent_id=uuid4(), display_name="入口")
    page = _contract("WebchatEntryPage")(items=[entry], page=1, page_size=75, total=1)
    assert set(page.model_dump()) == {"items", "page", "page_size", "total"}
    for changes in ({"page": 0}, {"page_size": 101}, {"page_size": True}):
        with pytest.raises(ValidationError):
            _contract("WebchatPageQuery")(**changes)


def test_b205_conversation_creation_and_history_restore_have_explicit_active_run() -> None:
    conversation_id, entry_id = uuid4(), uuid4()
    created = _contract("WebchatConversationCreated")(
        conversation_id=conversation_id, entry_id=entry_id, title="新会话", status="ACTIVE",
        last_seq=0, active_run=None,
    )
    assert created.last_seq == 0 and created.active_run is None
    assert _contract("WebchatConversationCreate")().model_dump() == {}
    run = {"id": str(uuid4()), "status": "RUNNING", "execution_status": {"stage": "RUNNING"}}
    detail = _contract("WebchatConversationDetail")(
        conversation_id=conversation_id, entry_id=entry_id, agent_id=uuid4(), title="历史会话",
        status="ACTIVE", last_seq=24, last_run_id=run["id"], active_run=run,
    )
    assert detail.active_run.id == detail.last_run_id and detail.active_run.confirmation is None
    row = _contract("WebchatConversationItem")(
        conversation_id=conversation_id, title="历史会话", create_time=ISSUED, last_message_at=ISSUED,
        last_run_id=run["id"], status="ACTIVE", active_run_status="RUNNING",
    )
    page = _contract("WebchatConversationPage")(items=[row], page=1, page_size=50, total=1)
    assert page.items[0].conversation_id == conversation_id
    with pytest.raises(ValidationError, match="last_seq"):
        _contract("WebchatConversationCreated")(**{**created.model_dump(), "last_seq": 1})


def test_b205_accepted_run_and_resume_do_not_guess_an_implicit_target() -> None:
    run_id, submission_id, source_id = uuid4(), uuid4(), uuid4()
    accepted = _contract("WebchatMessageAccepted")(
        conversation_id=uuid4(), run_id=run_id, status="CREATED",
        execution_status={"stage": "WAITING_RUNTIME", "reason": "RUNTIME_CAPACITY",
                          "queued_at": ISSUED, "deadline_at": ISSUED + timedelta(seconds=60)},
    )
    assert accepted.execution_status.stage == "WAITING_RUNTIME"
    confirmation = _contract("WebchatConfirmation")(
        submission_id=submission_id, source_event_id=source_id, prompt="请选择",
        options=[{"value": "original-17", "label": "保留原编号"}], expires_at=ISSUED,
    )
    active = _contract("WebchatActiveRun")(
        id=run_id, status="WAITING_INPUT", execution_status={"stage": "WAITING_INPUT"},
        confirmation=confirmation,
    )
    resume = _contract("WebchatResumeRequest")(
        submission_id=submission_id, source_event_id=source_id,
        message_id=str(uuid4()), selection="original-17",
    )
    assert resume.selection == active.confirmation.options[0].value
    with pytest.raises(ValidationError, match="status"):
        _contract("WebchatActiveRun")(id=run_id, status="COMPLETED", execution_status={"stage": "COMPLETED"})
    with pytest.raises(ValidationError, match="queued_at"):
        _contract("WebchatExecutionStatus")(stage="WAITING_MODEL")


def test_b205_client_config_is_a_whitelisted_projection_of_effective_settings() -> None:
    settings = WebchatSettings(history_page_size=75, text_max_bytes=8192, notification_poll_sec=2.5,
                               reconnect_base_ms=1000, reconnect_cap_ms=60000, reconnect_max_attempts=20)
    config = _contract("WebchatClientConfig").from_settings(
        settings_revision=7, transport_revision="transport-abc", webchat=settings,
        sse_event_max_bytes=262144, sse_heartbeat_sec=10,
    )
    wire = config.model_dump(mode="json")
    assert wire == {
        "settings_revision": 7, "transport_revision": "transport-abc",
        "webchat": {"history_page_size": 75, "text_max_bytes": 8192, "upload_max_bytes": 52428800,
                    "upload_max_per_message": 5, "notification_poll_sec": 2.5},
        "transport": {"sse_event_max_bytes": 262144, "sse_heartbeat_sec": 10,
                      "reconnect_base_ms": 1000, "reconnect_cap_ms": 60000, "reconnect_max_attempts": 20},
    }
    assert "access_ttl_days" not in wire["webchat"] and "session_ttl_hours" not in wire["webchat"]
    assert type(config).model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="internal_url"):
        _contract("WebchatClientConfig")(**{**wire, "internal_url": "https://private"})
    with pytest.raises(ValidationError):
        _contract("WebchatClientConfig")(**{
            **wire, "transport": {**wire["transport"], "reconnect_base_ms": 5000, "reconnect_cap_ms": 1000},
        })


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_b205_non_json_numbers_are_rejected_before_canonicalization(literal: str) -> None:
    limits = _contract("WebchatClientLimits")
    payload = ('{"history_page_size":50,"text_max_bytes":65536,"upload_max_bytes":52428800,'
               '"upload_max_per_message":5,"notification_poll_sec":' + literal + '}')
    with pytest.raises(ValidationError):
        limits.model_validate_json(payload)


@pytest.mark.parametrize("changes", [
    {"text_max_bytes": 1048577}, {"upload_max_per_message": 21}, {"notification_poll_sec": 0.5},
    {"history_page_size": 0}, {"notification_poll_sec": True},
])
def test_b205_client_limits_use_the_authoritative_business_schema(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _contract("WebchatClientLimits")(**{
            "history_page_size": 50, "text_max_bytes": 65536, "upload_max_bytes": 52428800,
            "upload_max_per_message": 5, "notification_poll_sec": 5, **changes,
        })


def test_b205_response_invariants_reject_ambiguous_restoration_or_pages() -> None:
    with pytest.raises(ValidationError, match="last_run_id"):
        _contract("WebchatConversationDetail")(
            conversation_id=uuid4(), entry_id=uuid4(), agent_id=uuid4(), title="title", status="ACTIVE",
            last_seq=1, last_run_id=None,
            active_run={"id": str(uuid4()), "status": "RUNNING", "execution_status": {"stage": "RUNNING"}},
        )
    with pytest.raises(ValidationError, match="page_size or total"):
        _contract("WebchatEntryPage")(
            items=[{"entry_id": str(uuid4()), "agent_id": str(uuid4()), "display_name": "entry"}],
            page=1, page_size=50, total=0,
        )
    with pytest.raises(ValidationError, match="deadline_at"):
        _contract("WebchatExecutionStatus")(
            stage="WAITING_RUNTIME", queued_at=ISSUED, deadline_at=ISSUED,
        )
    with pytest.raises(ValidationError, match="option values"):
        _contract("WebchatConfirmation")(
            submission_id=uuid4(), source_event_id=uuid4(), prompt="choose", expires_at=ISSUED,
            options=[{"value": "same", "label": "first"}, {"value": "same", "label": "second"}],
        )
