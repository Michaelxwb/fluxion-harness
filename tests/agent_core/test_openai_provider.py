import json
import logging
from typing import Any

import httpx
import pytest
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelRequestError,
    ModelRole,
    ModelUnavailableError,
    OpenAICompatibleProvider,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect

API_KEY = "sk-secret-value"
BASE_URL = "https://llm.test/v1"


def _request() -> ModelRequest:
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="be helpful"),
            ModelMessage(role=ModelRole.USER, content="hello"),
        ),
        temperature=0.2,
        max_tokens=64,
    )


def _provider(handler: Any, **kwargs: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=BASE_URL,
        model="gpt-4o-mini",
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _tool_choice_payload() -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def _request_with_tools() -> ModelRequest:
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=_request().messages,
        temperature=0.2,
        max_tokens=64,
        tools=(
            ToolDefinition(
                name="echo",
                description="echo",
                input_schema={"type": "object"},
                effect=ToolEffect.READ,
            ),
        ),
    )


async def test_success_maps_choices_tool_calls_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_tool_choice_payload())

    provider = _provider(handler)
    try:
        response = await provider.complete(_request_with_tools())
    finally:
        await provider.aclose()

    assert response.content == ""
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "echo"
    assert response.tool_calls[0].arguments == {"text": "hi"}
    assert (response.input_tokens, response.output_tokens) == (7, 3)


async def test_request_carries_headers_body_and_tool_schema() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        await provider.complete(_request_with_tools())
    finally:
        await provider.aclose()

    assert captured[0].method == "POST"
    assert captured[0].url.path == "/v1/chat/completions"
    assert captured[0].headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][0] == {"role": "system", "content": "be helpful"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["tools"][0]["function"]["name"] == "echo"


async def test_no_api_key_omits_authorization_header() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_tool_choice_payload())

    provider = OpenAICompatibleProvider(
        base_url="http://model.internal/v1",
        model="demo",
        api_key=None,
        transport=httpx.MockTransport(handler),
    )
    try:
        await provider.complete(_request())
    finally:
        await provider.aclose()

    assert len(captured) == 1
    assert "authorization" not in captured[0].headers


async def test_api_key_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        with caplog.at_level(logging.DEBUG):
            response = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert response.content == "ok"
    assert API_KEY not in caplog.text


async def test_rate_limit_reads_retry_after_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2.5"}, text="slow down")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRateLimitedError) as error:
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert error.value.retry_after == 2.5


async def test_rate_limit_without_valid_header_has_no_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "later"}, text="slow down")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRateLimitedError) as error:
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert error.value.retry_after is None


async def test_server_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_timeout_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_connect_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_client_error_maps_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_invalid_json_maps_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_invalid_tool_arguments_map_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {"id": "c1", "function": {"name": "echo", "arguments": "{bad"}}
                            ],
                        }
                    }
                ]
            },
        )

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_external_client_is_not_closed_by_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url=BASE_URL,
        model="gpt-4o-mini",
        api_key=API_KEY,
        client=client,
    )
    provider_typed: ModelProvider = provider
    try:
        await provider_typed.complete(_request())
        await provider.aclose()
        assert client.is_closed is False
    finally:
        await client.aclose()
