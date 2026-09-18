from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import httpx
import pytest
from muad_contracts import CredentialMode
from muad_platform_sdk import (
    GenericHttpAdapter,
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PlatformTarget,
    SecretValue,
)

adapter = GenericHttpAdapter()


def _platform(**adapter_config: Any) -> PlatformConfig:
    return PlatformConfig(
        key="demo",
        name="Demo Platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": "http://127.0.0.1:9/base"},
        adapter_key="generic-http",
        adapter_config=adapter_config,
        credential_mode=CredentialMode.USER_ONLY,
    )


def _secret(**payload: str) -> SecretValue:
    return SecretValue(value=json.dumps(payload), version="v1")


def _http_request(path: str = "/items") -> PlatformRequest:
    return PlatformRequest(target=PlatformTarget(method="POST", path=path), payload={"q": 1})


def test_schemas_mark_secret_fields_and_auth_options() -> None:
    assert adapter.platform_config_schema["properties"]["auth_scheme"]["enum"] == [
        "none",
        "bearer",
        "basic",
    ]
    properties = adapter.credential_schema["properties"]
    assert properties["token"]["x-secret"] is True
    assert properties["username"]["x-secret"] is True
    assert properties["password"]["x-secret"] is True
    assert adapter.session_mode == "NONE"
    assert not hasattr(adapter, "refresh")


async def test_prepare_request_injects_bearer_header_and_body() -> None:
    prepared = await adapter.prepare_request(
        _platform(auth_scheme="bearer"),
        None,
        _http_request(),
        _secret(token="tok-123"),
    )
    assert prepared.method == "POST"
    assert prepared.url == "http://127.0.0.1:9/base/items"
    assert prepared.headers["Authorization"] == "Bearer tok-123"
    assert prepared.headers["Content-Type"] == "application/json"
    assert json.loads(prepared.body or "{}") == {"q": 1}
    assert "tok-123" not in repr(prepared)


async def test_prepare_request_supports_basic_and_none() -> None:
    basic = await adapter.prepare_request(
        _platform(auth_scheme="basic"),
        None,
        _http_request(),
        _secret(username="u", password="p"),
    )
    assert basic.headers["Authorization"].startswith("Basic ")
    none = await adapter.prepare_request(
        _platform(auth_scheme="none"),
        None,
        _http_request(),
        None,
    )
    assert "Authorization" not in none.headers


async def test_prepare_request_rejects_missing_credential_and_logical_target() -> None:
    with pytest.raises(ValueError, match="credential required"):
        await adapter.prepare_request(_platform(auth_scheme="bearer"), None, _http_request(), None)
    with pytest.raises(ValueError, match="method/path"):
        await adapter.prepare_request(
            _platform(auth_scheme="none"),
            None,
            PlatformRequest(target=PlatformTarget(service="s", operation="o")),
            None,
        )


class _EchoHandler(BaseHTTPRequestHandler):
    last_headers: dict[str, str] = {}

    def do_POST(self) -> None:  # noqa: N802
        type(self).last_headers = dict(self.headers)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        del args


async def test_prepared_request_reaches_real_local_http_service() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        platform = _platform(auth_scheme="bearer").model_copy(
            update={"resolver_config": {"base_url": f"http://127.0.0.1:{server.server_port}"}}
        )
        prepared = await adapter.prepare_request(
            platform, None, _http_request("/echo"), _secret(token="integration-token")
        )
        async with httpx.AsyncClient() as client:
            response = await client.request(
                prepared.method, prepared.url, headers=prepared.headers, content=prepared.body
            )
        assert response.status_code == 200
        assert _EchoHandler.last_headers["Authorization"] == "Bearer integration-token"
        assert response.json() == {"q": 1}
    finally:
        server.shutdown()
        await asyncio.to_thread(server.server_close)
        thread.join(timeout=5)


async def test_authenticate_without_session_returns_none() -> None:
    session: PlatformSession | None = await adapter.authenticate(_platform(), _secret(token="t"))
    assert session is None
    assert await adapter.validate(_platform(), PlatformSession()) is True
