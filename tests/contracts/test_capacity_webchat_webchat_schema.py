"""B-202: Webchat settings, explicit retention semantics and deployment budgets."""

from dataclasses import asdict, fields, replace

import pytest
from muad_contracts.platform_settings import (
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
    validate_platform_settings,
)

pytestmark = pytest.mark.unit


def test_b202_defaults_use_one_typed_source() -> None:
    assert asdict(default_platform_settings().webchat) == {
        "access_ttl_days": 30,
        "session_ttl_hours": 12,
        "text_max_bytes": 65536,
        "upload_max_bytes": 52428800,
        "upload_max_per_message": 5,
        "upload_staging_ttl_sec": 86400,
        "history_page_size": 50,
        "notification_poll_sec": 5.0,
        "reconnect_base_ms": 500,
        "reconnect_cap_ms": 30000,
        "reconnect_max_attempts": 10,
        "stream_replay_retention_hours": 24,
        "notification_retention_days": 30,
        "history_retention_days": 0,
    }
    assert parse_platform_settings({"webchat": {}}) == default_platform_settings()


@pytest.mark.parametrize(
    "key,minimum,maximum",
    [
        ("access_ttl_days", 1, 365), ("session_ttl_hours", 1, 24),
        ("text_max_bytes", 1, 1048576), ("upload_max_per_message", 1, 20),
        ("upload_staging_ttl_sec", 60, 604800), ("history_page_size", 1, 100),
        ("reconnect_base_ms", 100, 5000), ("reconnect_cap_ms", 1000, 120000),
        ("reconnect_max_attempts", 1, 100), ("stream_replay_retention_hours", 1, 168),
        ("notification_retention_days", 1, 365), ("history_retention_days", 0, 3650),
    ],
)
def test_b202_integer_fields_enforce_inclusive_bounds(key: str, minimum: int, maximum: int) -> None:
    for value in (minimum, maximum):
        settings = parse_platform_settings({"webchat": {key: value}})
        assert getattr(settings.webchat, key) == value
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(PlatformSettingsError, match=rf"webchat\.{key}"):
            parse_platform_settings({"webchat": {key: value}})


@pytest.mark.parametrize(
    "key",
    [
        "access_ttl_days", "session_ttl_hours", "text_max_bytes", "upload_max_bytes",
        "upload_max_per_message", "upload_staging_ttl_sec", "history_page_size",
        "reconnect_base_ms", "reconnect_cap_ms", "reconnect_max_attempts",
        "stream_replay_retention_hours", "notification_retention_days", "history_retention_days",
    ],
)
@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, "1", None])
def test_b202_integer_fields_reject_coercion(key: str, value: object) -> None:
    with pytest.raises(PlatformSettingsError, match=rf"webchat\.{key}"):
        parse_platform_settings({"webchat": {key: value}})


@pytest.mark.parametrize("value", [1, 1.5, 60])
def test_b202_polling_interval_accepts_finite_numbers(value: int | float) -> None:
    settings = parse_platform_settings({"webchat": {"notification_poll_sec": value}})
    assert settings.webchat.notification_poll_sec == value


@pytest.mark.parametrize("value", [0, 60.1, float("nan"), float("inf"), True, "5", None])
def test_b202_polling_interval_rejects_invalid_numbers(value: object) -> None:
    with pytest.raises(PlatformSettingsError, match=r"webchat\.notification_poll_sec"):
        parse_platform_settings({"webchat": {"notification_poll_sec": value}})


@pytest.mark.parametrize("maximum", [999, 1000])
def test_b202_reconnect_base_cannot_exceed_cap(maximum: int) -> None:
    if maximum < 1000:
        with pytest.raises(PlatformSettingsError, match=r"webchat\.reconnect_cap_ms"):
            parse_platform_settings({"webchat": {"reconnect_base_ms": 1000, "reconnect_cap_ms": maximum}})
    else:
        settings = parse_platform_settings({
            "webchat": {"reconnect_base_ms": 1000, "reconnect_cap_ms": maximum},
        })
        assert settings.webchat.reconnect_base_ms == settings.webchat.reconnect_cap_ms == 1000
    with pytest.raises(PlatformSettingsError, match=r"webchat\.reconnect_base_ms"):
        parse_platform_settings({"webchat": {"reconnect_base_ms": 1001, "reconnect_cap_ms": 1000}})


@pytest.mark.parametrize("value", [0, 1, 3650])
def test_b202_zero_history_retention_is_explicit_and_round_trips(value: int) -> None:
    settings = parse_platform_settings({"webchat": {"history_retention_days": value}})
    assert settings.webchat.history_retention_days == value
    assert parse_platform_settings(asdict(settings)) == settings


@pytest.mark.parametrize(
    "key",
    ["access_ttl_days", "session_ttl_hours", "upload_max_bytes", "stream_replay_retention_hours"],
)
def test_b202_zero_does_not_disable_other_limits(key: str) -> None:
    with pytest.raises(PlatformSettingsError, match=rf"webchat\.{key}"):
        parse_platform_settings({"webchat": {key: 0}})


@pytest.mark.parametrize(
    "key,budget_key,budget",
    [
        ("text_max_bytes", "chat_message_hard_max_bytes", 2048),
        ("upload_max_bytes", "chat_upload_hard_max_bytes", 1024 * 1024),
    ],
)
def test_b202_request_limits_must_fit_injected_resource_budget(
    key: str, budget_key: str, budget: int,
) -> None:
    settings = parse_platform_settings({"webchat": {key: budget}}, **{budget_key: budget})
    assert getattr(settings.webchat, key) == budget
    with pytest.raises(PlatformSettingsError, match=rf"webchat\.{key}"):
        parse_platform_settings({"webchat": {key: budget + 1}}, **{budget_key: budget})


def test_b202_upload_budget_is_configurable_and_not_the_business_default() -> None:
    maximum = 128 * 1024 * 1024
    settings = parse_platform_settings(
        {"webchat": {"upload_max_bytes": maximum}}, chat_upload_hard_max_bytes=maximum,
    )
    assert settings.webchat.upload_max_bytes == maximum


@pytest.mark.parametrize("budget_key", ["chat_message_hard_max_bytes", "chat_upload_hard_max_bytes"])
@pytest.mark.parametrize("value", [0, -1, True, "1024", 1024.5])
def test_b202_invalid_injected_budgets_fail_explicitly(budget_key: str, value: object) -> None:
    with pytest.raises(PlatformSettingsError, match=budget_key):
        parse_platform_settings({}, **{budget_key: value})


@pytest.mark.parametrize("raw", [None, [], 1, True])
def test_b202_webchat_group_requires_object(raw: object) -> None:
    with pytest.raises(PlatformSettingsError, match="webchat"):
        parse_platform_settings({"webchat": raw})


@pytest.mark.parametrize("key", ["unknown", "token", "jwt_secret", "anonymous", "session_ttl_days"])
def test_b202_unknown_credentials_and_auth_bypass_keys_are_rejected(key: str) -> None:
    with pytest.raises(PlatformSettingsError, match="webchat"):
        parse_platform_settings({"webchat": {key: 1}})


@pytest.mark.parametrize(
    "changes",
    [
        {"access_ttl_days": True}, {"session_ttl_hours": 25}, {"text_max_bytes": 0},
        {"notification_poll_sec": float("nan")}, {"history_retention_days": -1},
        {"reconnect_base_ms": 2000, "reconnect_cap_ms": 1000},
    ],
)
def test_b202_direct_typed_instances_cannot_bypass_validation(changes: dict[str, object]) -> None:
    defaults = default_platform_settings()
    invalid = replace(defaults, webchat=replace(defaults.webchat, **changes))
    with pytest.raises(PlatformSettingsError, match="webchat"):
        validate_platform_settings(invalid)


def test_b202_field_units_are_declared_on_the_typed_schema() -> None:
    units = {item.name: item.metadata.get("unit") for item in fields(default_platform_settings().webchat)}
    assert units == {
        "access_ttl_days": "days", "session_ttl_hours": "hours",
        "text_max_bytes": "bytes", "upload_max_bytes": "bytes", "upload_max_per_message": None,
        "upload_staging_ttl_sec": "seconds", "history_page_size": None, "notification_poll_sec": "seconds",
        "reconnect_base_ms": "ms", "reconnect_cap_ms": "ms", "reconnect_max_attempts": None,
        "stream_replay_retention_hours": "hours", "notification_retention_days": "days",
        "history_retention_days": "days",
    }


@pytest.mark.parametrize(
    "budget_key,budget",
    [("chat_message_hard_max_bytes", 65535), ("chat_upload_hard_max_bytes", 52428799)],
)
def test_b202_defaults_and_direct_instances_must_fit_resource_budget(budget_key: str, budget: int) -> None:
    with pytest.raises(PlatformSettingsError, match=budget_key):
        parse_platform_settings(None, **{budget_key: budget})
    with pytest.raises(PlatformSettingsError, match=budget_key):
        validate_platform_settings(default_platform_settings(), **{budget_key: budget})
