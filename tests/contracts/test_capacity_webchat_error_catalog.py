"""B-207: stable localized codes, safe messages, standard envelopes and field details."""

from pathlib import Path
from string import Formatter

import pytest
from muad_api import AppError, ErrorCode
from muad_api.catalog import MessageCatalog
from muad_api.context import context_scope
from muad_api.response import build_response, ok, paginate
from muad_contracts.platform_settings import PlatformSettingsError, parse_platform_settings
from muad_contracts.webchat_events import WebchatCursorError

pytestmark = pytest.mark.unit
CATALOG_PATH = Path(__file__).resolve().parents[2] / "config/api-messages.yaml"

# Status is part of the browser/control protocol, independent from runtime exception text.
ERROR_STATUSES = {
    "PLATFORM_SETTINGS_UNAVAILABLE": 503,
    "CHAT_TOKEN_INVALID": 401, "CHAT_TOKEN_EXPIRED": 401, "CHAT_TOKEN_REVOKED": 401,
    "CHAT_SESSION_EXPIRED": 401, "CHAT_SESSION_REVOKED": 401,
    "AUTH_CONTEXT_CHANGED": 409, "CHAT_AUTH_RATE_LIMITED": 429, "REQUEST_BODY_TOO_LARGE": 413,
    "RUN_CAPACITY_EXCEEDED": 429, "RUN_QUEUE_TIMEOUT": 408,
    "MODEL_CAPACITY_UNAVAILABLE": 503, "MODEL_WAIT_TIMEOUT": 504,
    "MODEL_CAPACITY_DRAIN_REQUIRED": 409, "MODEL_CALL_OUTCOME_UNKNOWN": 409,
    "MODEL_CREDENTIAL_EXISTS": 409, "MODEL_CREDENTIAL_IN_USE": 409,
    "MODEL_DEPLOYMENT_EXISTS": 409, "MODEL_QUOTA_GROUP_IN_USE": 409,
    "MODEL_PROVIDER_MISMATCH": 422, "MODEL_CREDENTIAL_CHANGED": 409, "MODEL_CALL_LEASE_LOST": 409,
    "CONVERSATION_MODEL_MISMATCH": 409,
    "CURSOR_EXPIRED": 410, "CURSOR_AHEAD": 400, "CURSOR_RUN_MISMATCH": 400,
    "UPLOAD_EXPIRED": 410, "DELIVERY_RECEIPT_EXPIRED": 410, "DELIVERY_LEASE_LOST": 409,
    "EXECUTION_OUTCOME_UNKNOWN": 409,
}


@pytest.fixture(scope="module")
def catalog() -> MessageCatalog:
    return MessageCatalog(CATALOG_PATH)


@pytest.mark.parametrize("code,status", ERROR_STATUSES.items())
def test_b207_codes_and_http_statuses_are_explicit_and_registered(
    catalog: MessageCatalog, code: str, status: int,
) -> None:
    assert ErrorCode[code].value == code
    assert catalog.has(code) and catalog.spec(code).http_status == status


def test_b207_catalog_and_enum_match_bidirectionally_with_both_languages(catalog: MessageCatalog) -> None:
    assert catalog.codes() - {"0"} == {code.value for code in ErrorCode}
    formatter = Formatter()
    for code in ERROR_STATUSES:
        assert set(catalog.spec(code).messages) == {"zh-CN", "en-US"}
        for locale in ("zh-CN", "en-US"):
            template = catalog.spec(code).messages[locale]
            assert template.strip() and catalog.message(code, locale) == template
            assert not [field for _, field, _, _ in formatter.parse(template) if field is not None]


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_b207_error_envelopes_get_only_catalog_messages_and_keep_trace_context(
    catalog: MessageCatalog, locale: str,
) -> None:
    for code in ERROR_STATUSES:
        with context_scope(locale=locale, trace_id="trace-b207", request_id="request-b207"):
            error = AppError(ErrorCode[code], message_args={
                "token": "webchat-token-canary", "api_key": "model-key-canary",
                "provider_error": "private raw provider failure",
            })
            response = build_response(
                catalog, code=error.code, data=error.data, message_args=error.message_args,
            )
        wire = response.model_dump(mode="json")
        assert set(wire) == {"code", "msg", "data", "trace_id", "request_id", "timestamp"}
        assert wire["code"] == code and wire["msg"] == catalog.message(code, locale)
        assert wire["trace_id"] == "trace-b207" and wire["request_id"] == "request-b207"
        assert wire["data"] is None
        assert "canary" not in response.model_dump_json()
        assert "private raw provider failure" not in wire["msg"]
        assert str(error) == code


def test_b207_cursor_protocol_errors_all_have_catalog_codes(catalog: MessageCatalog) -> None:
    codes = WebchatCursorError.model_fields["code"].annotation.__args__
    assert set(codes) == {"CURSOR_EXPIRED", "CURSOR_AHEAD", "CURSOR_RUN_MISMATCH"}
    assert all(catalog.has(code) and ErrorCode[code].value == code for code in codes)


def test_b207_settings_field_details_remain_separate_from_the_localized_message(
    catalog: MessageCatalog,
) -> None:
    # Exercise the real schema failure and existing Console field mapping; no datastore or HTTP substitute.
    from muad_console_platform.application.platform_settings_guard import validation_detail

    with pytest.raises(PlatformSettingsError) as failure:
        parse_platform_settings({"webchat": {"reconnect_base_ms": 5000, "reconnect_cap_ms": 1000}})
    detail = validation_detail(failure.value)
    assert set(detail) == {"path", "message"} and detail["path"] == "webchat.reconnect_base_ms"
    error = AppError(ErrorCode.VALIDATION_FAILED, data={"details": [detail]})
    response = build_response(catalog, code=error.code, data=error.data)
    assert response.data == {"details": [detail]}
    assert response.msg == catalog.message("VALIDATION_FAILED", "zh-CN")


def test_b207_success_lists_reuse_the_standard_envelope_and_page_limits(catalog: MessageCatalog) -> None:
    page = paginate(items=[{"entry_id": "authorized-entry"}], page=1, page_size=100, total=1)
    response = ok(catalog, page)
    assert response.code == "0" and response.data == {
        "items": [{"entry_id": "authorized-entry"}], "page": 1, "page_size": 100, "total": 1,
    }
    for page_number, page_size in ((0, 50), (1, 0), (1, 101)):
        with pytest.raises(AppError) as failure:
            paginate(items=[], page=page_number, page_size=page_size, total=0)
        assert failure.value.code == "COMMON_VALIDATION_ERROR"
