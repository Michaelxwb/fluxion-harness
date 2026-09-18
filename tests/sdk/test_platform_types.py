import logging
from typing import Any

import pytest
from muad_contracts import CredentialMode
from muad_platform_sdk import (
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PlatformTarget,
    PreparedRequest,
    SecretValue,
    SessionMode,
)
from pydantic import ValidationError


def _platform_config(**overrides: Any) -> PlatformConfig:
    payload: dict[str, Any] = {
        "key": "mss",
        "name": "MSS Platform",
        "resolver_type": "BASE_URL",
        "resolver_config": {"base_url": "https://mss.example"},
        "adapter_key": "mssw",
        "adapter_config": {"login_endpoint": "/login"},
        "credential_mode": CredentialMode.USER_ONLY,
    }
    payload.update(overrides)
    return PlatformConfig(**payload)


def test_session_mode_members() -> None:
    assert {member.value for member in SessionMode} == {"NONE", "REQUEST_SIGNING", "SESSION"}


def test_secret_value_repr_and_str_never_leak_raw_value() -> None:
    secret = SecretValue(value="super-secret-ak", version="v1")

    for rendered in (repr(secret), str(secret), f"{secret}", f"{secret!r}"):
        assert "super-secret-ak" not in rendered

    assert secret.value == "super-secret-ak"
    assert secret.version == "v1"


def test_secret_value_is_log_safe(caplog: pytest.LogCaptureFixture) -> None:
    secret = SecretValue(value="super-secret-ak", version="v1")

    with caplog.at_level(logging.INFO):
        logging.getLogger("tests.sdk").info("credential=%s", secret)

    assert "super-secret-ak" not in caplog.text
    assert caplog.text != ""


def test_secret_value_requires_value_and_version() -> None:
    with pytest.raises(ValidationError):
        SecretValue.model_validate({"value": "ak-value"})

    with pytest.raises(ValidationError):
        SecretValue(value="", version="v1")

    with pytest.raises(ValidationError):
        SecretValue.model_validate({"value": "ak-value", "version": "v1", "extra": "nope"})


def test_platform_config_defaults_and_credential_mode() -> None:
    config = _platform_config()

    assert config.adapter_schema_version == "1"
    assert config.enabled is True
    assert config.credential_mode is CredentialMode.USER_ONLY

    with pytest.raises(ValidationError):
        PlatformConfig.model_validate(
            {"key": "mss", "name": "MSS", "resolver_type": "BASE_URL", "adapter_key": "mssw"}
        )

    with pytest.raises(ValidationError):
        _platform_config(credential_mode="NOT_A_MODE")


def test_platform_session_repr_hides_cached_tokens() -> None:
    session = PlatformSession(
        session_id="s-1",
        cookie="JSESSIONID=abc",
        access_token="token-abc",
        csrf_token="csrf-abc",
        adapter_state={"branch_tag": "main"},
    )

    rendered = repr(session)
    assert "JSESSIONID=abc" not in rendered
    assert "token-abc" not in rendered
    assert "csrf-abc" not in rendered
    assert session.session_id == "s-1"
    assert session.adapter_state == {"branch_tag": "main"}


def test_platform_target_accepts_logical_and_http_forms() -> None:
    logical = PlatformTarget(service="customer-service-mgr", operation="get_customer")
    assert logical.service == "customer-service-mgr"

    http = PlatformTarget(method="GET", path="/api/customer/C-1001")
    assert http.method == "GET"


def test_platform_target_rejects_mixed_or_incomplete_forms() -> None:
    for payload in (
        {},
        {"service": "s"},
        {"method": "GET"},
        {"service": "s", "operation": "o", "method": "GET"},
        {"service": "s", "method": "GET", "path": "/api"},
    ):
        with pytest.raises(ValidationError):
            PlatformTarget(**payload)

def test_platform_request_defaults_payload() -> None:
    request = PlatformRequest(target=PlatformTarget(service="svc", operation="op"))
    assert request.payload == {}


def test_prepared_request_hides_auth_headers_in_repr() -> None:
    prepared = PreparedRequest(
        method="POST",
        url="https://mss.example/api",
        headers={"Authorization": "Bearer top-secret"},
        body='{"a": 1}',
    )

    assert "top-secret" not in repr(prepared)
    assert prepared.method == "POST"
    assert prepared.headers["Authorization"] == "Bearer top-secret"


