"""Public subset of the Worker error envelope used by task tools.

Worker decides the business error; Runtime only keeps facts that can be checked
against the shared message catalog and a per-code allowlist. Free text from the
wire never reaches the model, audit or user copy.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache

from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_common import process_settings
from muad_contracts.scheduling import ScheduleDraftRequest

#: A diagnostic identifier, never free text: uppercase words/numbers, bounded.
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_ITEMS = 16
_MAX_VALUE = 64

_CONTRACT = "SCHEDULE_INVOCATION_CONTRACT_MISSING"
_CONTRACT_REASONS = frozenset({"CONTRACT_MISSING", "CONTRACT_INVALID"})
_CONTRACT_REPAIRS = frozenset({"REIMPORT_SKILL"})

#: Top-level Draft body fields. Nested payload details have their own paths below.
_DRAFT_FIELDS = frozenset(ScheduleDraftRequest.model_fields) | {"plan"}
_DRAFT_REASONS = frozenset(
    {
        "RECURRENCE_INVALID",
        "PLAN_ENVELOPE_CONFLICT",
        "PLAN_ENVELOPE_INVALID",
        "SECRET_DETECTED",
        "MULTIPLE_TARGETS",
    }
)
_INPUT_REASONS = frozenset(
    """
    CHANNEL_MISMATCH INVALID_TYPE INVALID INVALID_OPTION UNKNOWN_OPTION DUPLICATE_OPTION
    BOOLEAN_MUST_BE_FLAG MISSING_VALUE MISSING_REQUIRED UNKNOWN_ARGUMENT INVALID_FORMAT
    INVALID_VALUE POSITIONAL_NOT_SUPPORTED SHORT_OPTION_NOT_SUPPORTED CONTRACT_INVALID
    """.split()
)
_INPUT_REPAIRS = frozenset(
    """
    PROVIDE_SKILL_ARGV PROVIDE_SKILL_INPUT USE_DECLARED_CHANNEL USE_LONG_OPTIONS
    FIX_SKILL_ARGV FIX_SKILL_INPUT REIMPORT_SKILL
    """.split()
)
_DEPENDENCY_REPAIRS = frozenset(
    """
    UPDATE_SCHEDULE_DEPENDENCY ENABLE_AGENT RESTORE_AGENT_GRANT REIMPORT_SKILL
    CONFIGURE_AGENT_MODEL RESTORE_MODEL_CREDENTIAL RESTORE_TOOL_GRANT FIX_SCHEDULE_ARGV
    REDUCE_SCHEDULE_DEPENDENCIES RETRY_LATER
    """.split()
)
_DELIVERY_REASONS = frozenset({"ENTRY_CHANNEL_UNSUPPORTED"})
_DELIVERY_REPAIRS = frozenset({"CHANGE_DELIVERY_INTENT"})

_INPUT_FIELDS = frozenset({"payload.argv", "payload.input"})
#: A shape check only. The Runtime re-checks these against the latest Skill contract.
_VALUE_TOKEN = re.compile(r"^-{0,2}[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}$")


@cache
def error_catalog() -> MessageCatalog:
    return MessageCatalog(process_settings().api_messages_file)


def _bounded(
    value: object,
    *,
    allowed: frozenset[str] | None = None,
    pattern: re.Pattern[str] | None = None,
) -> str | None:
    """Return a bounded, allowlisted string; anything unverifiable is dropped."""
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_VALUE:
        return None
    if allowed is not None and value not in allowed:
        return None
    if pattern is not None and pattern.fullmatch(value) is None:
        return None
    return value


def _bounded_list(
    value: object,
    *,
    allowed: frozenset[str] | None = None,
    pattern: re.Pattern[str] | None = None,
) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    items = [
        text
        for item in value[:_MAX_ITEMS]
        if (text := _bounded(item, allowed=allowed, pattern=pattern)) is not None
    ]
    return items or None


def _put(detail: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        detail[key] = value


def public_detail(code: str, raw: object) -> dict[str, object]:
    """Project Worker `data` onto the documented public keys for ``code``."""
    if not isinstance(raw, Mapping):
        return {}
    if code == _CONTRACT:
        if raw.get("field") != "payload.skill_id":
            return {}
        reason = _bounded(raw.get("reason"), allowed=_CONTRACT_REASONS)
        if reason is None:
            return {}
        detail: dict[str, object] = {"field": "payload.skill_id", "reason": reason}
        _put(detail, "repair", _bounded(raw.get("repair"), allowed=_CONTRACT_REPAIRS))
        return detail

    detail = {}
    if code == "SCHEDULE_DRAFT_INVALID":
        _put(detail, "reason", _bounded(raw.get("reason"), allowed=_DRAFT_REASONS))
        _put(detail, "field", _bounded(raw.get("field"), allowed=_DRAFT_FIELDS))
        _put(detail, "fields", _bounded_list(raw.get("fields"), allowed=_DRAFT_FIELDS))
    elif code == "SCHEDULE_PARAMETER_INCOMPLETE":
        _put(detail, "fields", _bounded_list(raw.get("fields"), allowed=_DRAFT_FIELDS))
    elif code == "SCHEDULE_NO_FUTURE_OCCURRENCE":
        _put(detail, "reason", _bounded(raw.get("reason"), allowed=frozenset({"NO_FUTURE_OCCURRENCE"})))
    elif code in {"SCHEDULE_ARGV_INVALID", "SCHEDULE_INPUT_INVALID"}:
        _put(detail, "field", _bounded(raw.get("field"), allowed=_INPUT_FIELDS))
        _put(detail, "reason", _bounded(raw.get("reason"), allowed=_INPUT_REASONS))
        _put(detail, "repair", _bounded(raw.get("repair"), allowed=_INPUT_REPAIRS))
        _put(
            detail,
            "allowed_values",
            _bounded_list(raw.get("allowed_values"), pattern=_VALUE_TOKEN),
        )
    elif code in {
        "SCHEDULE_DEPENDENCY_MISSING",
        "SCHEDULE_GRANT_DENIED",
        "SCHEDULE_ADMISSION_FAILED",
        "SCHEDULE_TOOL_WILDCARD_UNSUPPORTED",
    }:
        _put(detail, "repair", _bounded(raw.get("repair"), allowed=_DEPENDENCY_REPAIRS))
    elif code == "SCHEDULE_DELIVERY_UNSUPPORTED":
        _put(detail, "reason", _bounded(raw.get("reason"), allowed=_DELIVERY_REASONS))
        _put(detail, "repair", _bounded(raw.get("repair"), allowed=_DELIVERY_REPAIRS))
    return detail


def public_message(code: str, locale: str) -> str:
    """Localized catalog description; unknown codes fall back to the generic one."""
    catalog = error_catalog()
    resolved = code if catalog.has(code) else ErrorCode.COMMON_INTERNAL_ERROR.value
    return catalog.message(resolved, locale)


class WorkerResponseError(AppError):
    """Worker non-success response reduced to validated public facts.

    ``outcome_known`` is only true for a registered error code with a 4xx
    response: that is the Worker's explicit pre-write rejection. Unknown codes,
    5xx responses, malformed bodies and transport failures stay unknown.
    """

    def __init__(self, payload: object, locale: str, *, outcome_known: bool, sent: bool) -> None:
        envelope = payload if isinstance(payload, Mapping) else {}
        catalog = error_catalog()
        raw_code = envelope.get("code")
        safe_code = raw_code if isinstance(raw_code, str) and _CODE.fullmatch(raw_code) else None
        registered = safe_code is not None and catalog.has(safe_code)
        code = safe_code if safe_code is not None else ErrorCode.COMMON_INTERNAL_ERROR.value
        message = public_message(code, locale)
        data = public_detail(code, envelope.get("data")) if registered else {}
        super().__init__(code, data=data)

        self.public_message = message
        self.message_source = (
            "PLATFORM" if registered and envelope.get("msg") == message else "CATALOG"
        )
        self.outcome_known = outcome_known and registered
        self.sent = sent
        self.retryable = catalog.spec(code).retryable if self.outcome_known else None
