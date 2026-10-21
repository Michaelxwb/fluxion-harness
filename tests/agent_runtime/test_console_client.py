import json
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
            "definitions": [],
        }
    )
    assert server.catalog_revision == 3
    assert server.definitions == []


async def test_fetch_admission_materials_is_one_request_and_parses_credentials() -> None:
    """受理合并取数：一次 POST 取回定义 + 设置快照 + 凭据（含 MCP 可选鉴权）。"""
    captured: list[httpx.Request] = []
    mcp_id = uuid.uuid4()
    definition = _resolve_data()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "成功",
                "data": {
                    "definition": definition,
                    "settings_revision": 3,
                    "settings": {"agent": {"max_turns": 9}},
                    "credentials": {
                        "model": {"model_id": definition["model"]["id"], "api_key": "sk-live"},
                        "mcp_servers": [
                            {"mcp_server_id": str(mcp_id), "auth_secret": "mcp-secret"},
                            {"mcp_server_id": str(uuid.uuid4()), "auth_secret": None},
                        ],
                    },
                },
            },
        )

    client = ConsoleResolveClient(
        "http://console", service_token="svc", transport=httpx.MockTransport(handler)
    )
    agent_id, actor_id, execution_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    materials = await client.fetch_admission_materials(
        tenant_id="tenant-1",
        agent_id=agent_id,
        actor_user_id=actor_id,
        execution_id=execution_id,
        channel="WEBCHAT",
        trace_id="trace-1",
    )
    await client.aclose()

    assert len(captured) == 1, "合并取数必须是一次往返"
    request = captured[0]
    assert request.url.path == "/internal/runtime/admission-materials"
    assert request.headers["X-Internal-Service"] == "svc"
    assert request.headers["X-Tenant-Id"] == "tenant-1"
    assert request.headers["X-Trace-Id"] == "trace-1"
    body = json.loads(request.content)
    assert body == {
        "agent_id": str(agent_id),
        "actor_user_id": str(actor_id),
        "execution_id": str(execution_id),
        "channel": "WEBCHAT",
    }
    assert materials.definition.agent.key == "agent-key"
    assert materials.settings.revision == 3
    assert materials.settings.settings == {"agent": {"max_turns": 9}}
    assert materials.model.api_key == "sk-live"
    assert materials.mcp_secrets == {str(mcp_id): "mcp-secret"}


async def test_fetch_admission_materials_requires_api_key() -> None:
    """没有当前认证值即 `CREDENTIAL_MISSING`：不拿定义里的旧密钥兜底。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "成功",
                "data": {
                    "definition": _resolve_data(),
                    "settings_revision": 0,
                    "settings": {},
                    "credentials": {"model": {"api_key": None}, "mcp_servers": []},
                },
            },
        )

    client = ConsoleResolveClient("http://console", transport=httpx.MockTransport(handler))
    with pytest.raises(AppError) as raised:
        await client.fetch_admission_materials(
            tenant_id="t",
            agent_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
        )
    await client.aclose()
    assert raised.value.code == "CREDENTIAL_MISSING"


async def test_fetch_admission_materials_maps_error_envelope() -> None:
    """Console 侧拒绝（如服务身份不符）按信封错误码上抛，不吞成 500 语义。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "FORBIDDEN", "msg": "denied", "data": None})

    client = ConsoleResolveClient("http://console", transport=httpx.MockTransport(handler))
    with pytest.raises(AppError) as raised:
        await client.fetch_admission_materials(
            tenant_id="t",
            agent_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
            execution_id=uuid.uuid4(),
        )
    await client.aclose()
    assert raised.value.code == "FORBIDDEN"
