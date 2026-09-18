import uuid

import httpx
import pytest
from muad_agent_runtime.infrastructure.console_client import (
    RESOLVE_DEFINITION_PATH,
    ConsoleResolveClient,
    error_code_from_payload,
)
from muad_api import AppError
from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse

REQUEST = ResolveDefinitionRequest(agent_id=uuid.uuid4(), actor_user_id=uuid.uuid4(), channel="WECOM")


def _resolve_data() -> dict[str, object]:
    return {
        "agent": {
            "id": str(uuid.uuid4()),
            "key": "agent-key",
            "revision": 1,
            "instructions": "be helpful",
            "runtime_config": {},
        },
        "model": {
            "id": str(uuid.uuid4()),
            "revision": 1,
            "protocol": "OPENAI",
            "model_id": "gpt-4o-mini",
            "base_url": "https://api.example.com/v1",
            "api_key": None,
            "params": {},
        },
        "skills": [],
        "mcp_servers": [],
    }


def _response_payload() -> dict[str, object]:
    return {"code": "0", "msg": "成功", "data": _resolve_data()}


def test_error_code_from_nested_envelope() -> None:
    payload = {"error": {"code": "AGENT_ACCESS_DENIED", "message": "denied"}}
    assert error_code_from_payload(payload) == "AGENT_ACCESS_DENIED"


def test_error_code_from_flat_envelope_and_fallback() -> None:
    assert error_code_from_payload({"code": "AGENT_NOT_FOUND"}) == "AGENT_NOT_FOUND"
    assert error_code_from_payload("not-json") == "COMMON_INTERNAL_ERROR"
    assert error_code_from_payload({"error": {"message": "no code"}}) == "COMMON_INTERNAL_ERROR"


async def test_resolve_posts_request_and_parses_response() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_response_payload())

    client = ConsoleResolveClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        resolved = await client.resolve(REQUEST, tenant_id="tenant-1")
    finally:
        await client.aclose()

    assert isinstance(resolved, ResolveDefinitionResponse)
    assert resolved.agent.key == "agent-key"
    assert len(captured) == 1
    assert captured[0].url.path == RESOLVE_DEFINITION_PATH
    assert captured[0].method == "POST"
    assert captured[0].headers["x-tenant-id"] == "tenant-1"


async def test_resolve_maps_error_envelope_to_app_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "AGENT_ACCESS_DENIED", "message": "denied"}},
        )

    client = ConsoleResolveClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(REQUEST, tenant_id="tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "AGENT_ACCESS_DENIED"


async def test_resolve_raises_internal_error_without_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = ConsoleResolveClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(REQUEST, tenant_id="tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"


async def test_resolve_rejects_missing_envelope_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "ok"})

    client = ConsoleResolveClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(REQUEST, tenant_id="tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
