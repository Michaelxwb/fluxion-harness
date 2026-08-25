from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest
from tests.runtime_helpers import publish_resource

from fluxion.plugins.contracts import (
    CapabilityDescriptor,
    ModelMessage,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    ToolCall,
    ToolDefinition,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader
from fluxion.plugins.model_provider import (
    ModelProviderRegistry,
    OpenAICompatibleHTTPModelProvider,
    StubModelProviderPlugin,
    _stream_chunk_content,
    _tool_call,
)
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


class SlowModelProviderPlugin:
    manifest = PluginManifest(
        plugin_id="slow",
        version="1",
        plugin_type=PluginType.MODEL_PROVIDER,
        entrypoint="tests.slow:Provider",
        trust_level=TrustLevel.TRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )

    def __init__(self) -> None:
        self.setup_called = False

    async def setup(self, ctx: PluginContext) -> None:
        self.setup_called = ctx.tenant_id == "tenant-a"

    async def shutdown(self) -> None:
        return None

    def capabilities(self) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                capability_id="model.slow",
                kind="model_provider",
                version="1",
                metadata={"provider_id": "slow"},
            )
        ]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        await asyncio.sleep(0.05)
        return ModelResponse(provider_id="slow", content="too late")


@pytest.mark.asyncio
async def test_S_R13_agentloop_uses_model_provider_plugin_tool_calling_and_failover(
    sqlite_store: RegistryStore,
) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={
            "prompt": "保持严谨",
            "model_policy": {
                "provider": "slow",
                "failover": ["stub"],
                "timeout_ms": 5,
            },
            "allowed_skills": [],
        },
    )
    registry = ModelProviderRegistry()
    loader = PluginLoader(model_provider_registry=registry)
    slow = SlowModelProviderPlugin()
    stub = StubModelProviderPlugin(
        provider_id="stub",
        response=ModelResponse(
            provider_id="stub",
            content="需要调用工具",
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    name="lookup",
                    arguments={"query": "fluxion"},
                )
            ],
        ),
    )

    await loader.load(slow, PluginContext(tenant_id="tenant-a"))
    await loader.load(stub, PluginContext(tenant_id="tenant-a"))
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
        model_providers=registry,
    )

    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    result = await runtime.run_step(
        context,
        "查询 Fluxion",
        tools=[
            ToolDefinition(
                name="lookup",
                description="fixture tool",
                parameters={"type": "object"},
            )
        ],
    )

    assert slow.setup_called is True
    assert result.model_response is not None
    assert result.model_response.provider_id == "stub"
    assert result.model_response.tool_calls[0].name == "lookup"
    assert result.output == "需要调用工具"
    assert any(event.name == "model.timeout" for event in context.trace)
    assert any(
        event.name == "model.completed" and event.attributes["provider_id"] == "stub"
        for event in context.trace
    )


def _provider_with_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
    *,
    max_retries: int = 1,
) -> OpenAICompatibleHTTPModelProvider:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=handler, timeout=kwargs.get("timeout", 5.0)
        ),
    )
    return OpenAICompatibleHTTPModelProvider(
        provider_id="test",
        api_base_url="https://example.com",
        model="m",
        max_retries=max_retries,
    )


@pytest.mark.asyncio
async def test_S_R13_http_4xx_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    provider = _provider_with_transport(
        monkeypatch, httpx.MockTransport(handler), max_retries=3
    )
    with pytest.raises(ModelProviderError, match="401"):
        await provider._post_with_retry({"messages": []})
    assert calls == 1


@pytest.mark.asyncio
async def test_S_R13_http_5xx_is_retried_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider_with_transport(
        monkeypatch, httpx.MockTransport(handler), max_retries=2
    )
    payload = await provider._post_with_retry({"messages": []})
    assert calls == 3
    choices = cast(list[dict[str, dict[str, str]]], payload["choices"])
    assert choices[0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_S_R13_non_json_body_raises_model_provider_error_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="this is not json")

    provider = _provider_with_transport(
        monkeypatch, httpx.MockTransport(handler), max_retries=3
    )
    with pytest.raises(ModelProviderError, match="invalid json"):
        await provider._post_with_retry({"messages": []})
    assert calls == 1


@pytest.mark.asyncio
async def test_S_F10_http_429_rate_limit_is_retried_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10：429（rate-limit）是瞬时 4xx，应退避重试（此前与 401 一样立即抛）。
    退避后第二次 attempt 返回 200，payload 正常解析。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider_with_transport(
        monkeypatch, httpx.MockTransport(handler), max_retries=2
    )
    payload = await provider._post_with_retry({"messages": []})
    assert calls == 2
    assert cast(list, payload["choices"])[0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_S_F10_http_408_request_timeout_is_retried_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10：408（request timeout）是瞬时 4xx，应退避重试。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(408, json={"error": "timeout"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider_with_transport(
        monkeypatch, httpx.MockTransport(handler), max_retries=2
    )
    payload = await provider._post_with_retry({"messages": []})
    assert calls == 2
    assert cast(list, payload["choices"])[0]["message"]["content"] == "ok"


def test_S_R13_tool_call_parses_string_json_arguments() -> None:
    call = _tool_call(
        {
            "id": "call-1",
            "function": {"name": "lookup", "arguments": '{"query": "fluxion", "n": 3}'},
        }
    )
    assert call is not None
    assert call.name == "lookup"
    assert call.arguments == {"query": "fluxion", "n": 3}


def test_S_R13_tool_call_unparseable_arguments_keeps_raw() -> None:
    call = _tool_call(
        {"id": "call-2", "function": {"name": "lookup", "arguments": "not-json"}}
    )
    assert call is not None
    assert call.arguments == {"raw": "not-json"}


@pytest.mark.asyncio
async def test_S_R13_stream_yields_tokens_and_ignores_non_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        body = (
            b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body)

    provider = _provider_with_transport(monkeypatch, httpx.MockTransport(handler))
    tokens = [
        token
        async for token in provider.stream(
            ModelRequest(messages=[ModelMessage(role="user", content="hi")])
        )
    ]

    assert tokens == ["你", "好"]


def test_S_R13_stream_chunk_content_ignores_non_content_lines() -> None:
    assert _stream_chunk_content('data: {"choices":[{"delta":{"content":"a"}}]}') == "a"
    assert _stream_chunk_content("data: [DONE]") is None
    assert _stream_chunk_content("event: message") is None
    assert _stream_chunk_content("data: not-json") is None
    assert _stream_chunk_content('data: {"choices":[{"delta":{}}]}') is None
