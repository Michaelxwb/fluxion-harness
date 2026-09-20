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


def http_transport_factory(handler):
    return httpx.MockTransport(handler)


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


# ---- B-103（08 TASK-003）：resolve-credentials 客户端 + MCP catalog 校验 ----

async def test_b103_resolve_credentials_posts_and_returns_in_memory_only() -> None:
    """[B-103] 凭据端点返回的认证数据只进入内存对象；请求透传 tenant/actor。"""
    import json as json_module

    from muad_agent_runtime.infrastructure.console_client import (
        RESOLVE_CREDENTIALS_PATH,
        ConsoleCredentialsClient,
    )

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["tenant"] = request.headers.get("X-Tenant-Id")
        seen["trace"] = request.headers.get("X-Trace-Id")
        seen["body"] = json_module.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "model": {"id": "m-1", "api_key": "sk-live-key"},
                    "mcp_servers": [{"mcp_server_id": "s-1", "auth_secret": "sec-1"}],
                },
            },
        )

    client = ConsoleCredentialsClient("http://console", transport=httpx.MockTransport(handler))
    result = await client.resolve_credentials(
        tenant_id="t-1",
        trace_id="tr-1",
        payload={
            "execution_ref": {"type": "RUN", "id": "r-1"},
            "actor_user_id": "u-1",
            "model_id": "m-1",
            "mcp_server_ids": ["s-1"],
        },
    )
    await client.aclose()

    assert seen["path"] == RESOLVE_CREDENTIALS_PATH
    assert seen["tenant"] == "t-1"
    assert seen["trace"] == "tr-1"
    body = seen["body"]
    assert body["execution_ref"] == {"type": "RUN", "id": "r-1"}  # actor 透传
    assert result["model"]["api_key"] == "sk-live-key"  # 仅内存对象
    assert result["mcp_servers"][0]["auth_secret"] == "sec-1"


async def test_b103_credentials_error_keeps_registered_code() -> None:
    """[B-103] 授权/缺失错误保持登记 code（CREDENTIAL_MISSING 不被吞）。"""
    from muad_agent_runtime.infrastructure.console_client import (
        ConsoleCredentialsClient,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": "CREDENTIAL_MISSING", "msg": "missing"})

    client = ConsoleCredentialsClient("http://console", transport=http_transport_factory(handler))
    with pytest.raises(AppError) as exc:
        await client.resolve_credentials(tenant_id="t-1", payload={"model_id": "m"})
    await client.aclose()
    assert exc.value.code == "CREDENTIAL_MISSING"


def test_b103_resolve_response_validates_mcp_catalog_fields() -> None:
    """[B-103] 缺失 MCP catalog（revision/hash）不能静默放行：契约校验拒绝。"""
    from muad_contracts import ResolvedMcpServer
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ResolvedMcpServer.model_validate({"mcp_server_id": str(uuid.uuid4()), "key": "k", "endpoint": "http://x"})
    server = ResolvedMcpServer.model_validate(
        {
            "mcp_server_id": str(uuid.uuid4()),
            "key": "k",
            "endpoint": "http://x",
            "catalog_revision": 3,
            "catalog_hash": "sha256:" + "a" * 64,
            "tools": [],
        }
    )
    assert server.catalog_revision == 3
