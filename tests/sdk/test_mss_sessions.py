import importlib.util
import json
import secrets
import threading
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from muad_platform_sdk import (
    MSSPAdapter,
    MSSWAdapter,
    PlatformAuthError,
    PlatformConfig,
    PlatformRequest,
    PlatformTarget,
    SecretValue,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def platform_server():
    spec = importlib.util.spec_from_file_location(
        "platform_server_server", Path(__file__).parents[2] / "tests/sdk/platform_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ak, sk = secrets.token_hex(12), secrets.token_hex(24)
    server = module.AuthPlatformServer(("127.0.0.1", 0), ak, sk)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    platform = PlatformConfig(
        key="platform_server",
        name="Test platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": f"http://127.0.0.1:{server.server_port}"},
        adapter_key="mssw-hmac-session",
        adapter_config={"csrf_enabled": True},
        credential_mode="USER_ONLY",
    )
    yield server, platform, SecretValue(value=json.dumps({"ak": ak, "sk": sk}), version="1")
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("adapter_type", [MSSPAdapter, MSSWAdapter])
async def test_real_login_validate_query_revoke(platform_server, adapter_type):
    server, platform, credential = platform_server
    adapter = adapter_type()
    session = await adapter.authenticate(platform, credential)
    assert await adapter.validate(platform, session)
    request = PlatformRequest(
        target=PlatformTarget(method="GET", path="/events"), payload={"company_id": "123"}
    )
    prepared = await adapter.prepare_request(platform, session, request, None)
    assert prepared.headers[adapter.csrf_header] == server.csrf_token
    async with httpx.AsyncClient() as client:
        response = await client.get(prepared.url, headers=prepared.headers)
    assert response.json() == {"code": 0}
    assert "company_id=123" in prepared.url
    assert credential.value not in repr(session)
    server.sessions.clear()
    assert not await adapter.validate(platform, session)


async def test_wrong_sk_rejected_even_http_200(platform_server):
    _, platform, credential = platform_server
    data = json.loads(credential.value)
    data["sk"] = secrets.token_hex(24)
    with pytest.raises(PlatformAuthError, match="AUTH_REJECTED"):
        await MSSWAdapter().authenticate(platform, SecretValue(value=json.dumps(data), version="2"))


async def test_changed_platform_and_cross_origin_refused(platform_server):
    _, platform, credential = platform_server
    adapter = MSSWAdapter()
    session = await adapter.authenticate(platform, credential)
    changed = platform.model_copy(update={"adapter_config": {"timezone": "+00:00"}})
    assert not await adapter.validate(changed, session)
    request = PlatformRequest(target=PlatformTarget(method="GET", path="https://other.invalid/events"))
    with pytest.raises(PlatformAuthError, match="TARGET_INVALID"):
        await adapter.prepare_request(platform, session, request, None)


def test_schema_parity_public_config_private_aksk():
    schema = MSSPAdapter.credential_schema
    assert set(schema["properties"]) == {"ak", "sk"}
    validate({"ak": "platform_server", "sk": "platform_server"}, schema)
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        validate(
            {"ak": "platform_server", "sk": "platform_server", "baseUrl": "http://example.invalid"}, schema
        )
    validate({"host_header": "internal.example", "csrf_enabled": True}, MSSPAdapter.platform_config_schema)


async def test_incorrect_ak_and_missing_config_in_credentials(platform_server):
    _, platform, original = platform_server
    payload = json.loads(original.value)
    payload["ak"] = "unknown-account"
    with pytest.raises(PlatformAuthError, match="AUTH_REJECTED"):
        await MSSWAdapter().authenticate(platform, SecretValue(value=json.dumps(payload), version="2"))
    payload["baseUrl"] = platform.resolver_config["base_url"]
    with pytest.raises(PlatformAuthError, match="CREDENTIAL_INVALID"):
        await MSSWAdapter().authenticate(platform, SecretValue(value=json.dumps(payload), version="3"))


async def test_nonce_replay_and_timestamp(platform_server):
    import time

    from muad_platform_sdk.adapter.mssp_signing import login_authorization

    server, platform, credential = platform_server
    url = platform.resolver_config["base_url"] + "/gateway/mss-auth-acl-service/v1/certification/login_agent"
    data = json.loads(credential.value)
    headers = {
        "Content-Type": "application/json",
        "X-Branch-Tag": "MSSW-ADAPTER",
        "Agent-Nonce": secrets.token_hex(16),
        "x-csrftoken": server.csrf_token,
    }
    headers["Authorization"] = login_authorization(url, data["ak"], data["sk"], int(time.time()))
    async with httpx.AsyncClient() as client:
        assert (await client.post(url, headers=headers, content=b"")).json()["code"] == 0
        assert (await client.post(url, headers=headers, content=b"")).json()["code"] == 9348
        headers["Agent-Nonce"] = secrets.token_hex(16)
        headers["Authorization"] = login_authorization(url, data["ak"], data["sk"], int(time.time()) - 120)
        assert (await client.post(url, headers=headers, content=b"")).json()["code"] == 9348


async def test_cookie_path_and_expiry(platform_server):
    from datetime import UTC, datetime, timedelta

    _, platform, credential = platform_server
    adapter = MSSWAdapter()
    session = await adapter.authenticate(platform, credential)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not await adapter.validate(platform, session)
    session.expires_at = datetime.now(UTC) + timedelta(seconds=100)
    cookies = json.loads(session.cookie)
    for cookie in cookies:
        cookie["path"] = "/private"
    session.cookie = json.dumps(cookies)
    with pytest.raises(PlatformAuthError, match="SESSION_EXPIRED"):
        await adapter.prepare_request(
            platform, session, PlatformRequest(target=PlatformTarget(method="GET", path="/public")), None
        )


def test_console_registry_exposes_real_adapters():
    from muad_console_platform.application.platform_adapter_service import build_default_registry

    registry = build_default_registry(("mssp-hmac-session", "mssw-hmac-session"))
    assert isinstance(registry.get("mssp-hmac-session"), MSSPAdapter)
    assert isinstance(registry.get("mssw-hmac-session"), MSSWAdapter)


async def test_csrf_only_is_not_login(platform_server):
    server, platform, credential = platform_server
    server.issue_login_cookie = False
    with pytest.raises(PlatformAuthError, match="LOGIN_NO_COOKIE"):
        await MSSWAdapter().authenticate(platform, credential)


async def test_business_request_without_csrf_rejected(platform_server):
    _, platform, credential = platform_server
    adapter = MSSWAdapter()
    session = await adapter.authenticate(platform, credential)
    request = PlatformRequest(target=PlatformTarget(method="GET", path="/events"))
    prepared = await adapter.prepare_request(platform, session, request, None)
    del prepared.headers[adapter.csrf_header]
    async with httpx.AsyncClient() as client:
        response = await client.get(prepared.url, headers=prepared.headers)
    assert response.status_code == 401
