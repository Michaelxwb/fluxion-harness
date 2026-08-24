from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import cast

import httpx

from fluxion.plugins.contracts import (
    CapabilityDescriptor,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelProviderTimeoutError,
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


class ModelProviderNotFoundError(ModelProviderError):
    code = "model_provider_not_found"


class ModelProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider_id: str, provider: ModelProvider) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id is required")
        self._providers[provider_id] = provider

    def resolve(self, provider_id: str) -> ModelProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ModelProviderNotFoundError(f"model provider {provider_id} not found")
        return provider

    def provider_ids(self) -> list[str]:
        return list(self._providers)


@dataclass(slots=True)
class StubModelProviderPlugin:
    provider_id: str
    response: ModelResponse
    setup_called: bool = False

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1",
            plugin_type=PluginType.MODEL_PROVIDER,
            entrypoint="fluxion.plugins.model_provider:StubModelProviderPlugin",
            trust_level=TrustLevel.TRUSTED,
            permissions=[],
            dependencies=[],
            compatibility={"fluxion": ">=0.1"},
            execution_mode=PluginExecutionMode.IN_PROCESS,
        )

    async def setup(self, ctx: PluginContext) -> None:
        self.setup_called = bool(ctx.tenant_id)

    async def shutdown(self) -> None:
        self.setup_called = False

    def capabilities(self) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                capability_id=f"model.{self.provider_id}",
                kind="model_provider",
                version="1",
                metadata={"provider_id": self.provider_id, "tool_calling": True},
            )
        ]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return self.response


class OpenAICompatibleHTTPModelProvider:
    def __init__(
        self,
        *,
        provider_id: str,
        api_base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        api_key: str | None = None,
        max_retries: int = 1,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._provider_id = provider_id
        self._api_base_url = api_base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._max_retries = max(0, max_retries)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload = _request_payload(request, self._model)
        response = await self._post_with_retry(payload)
        return _response_from_openai(self._provider_id, response)

    async def _post_with_retry(self, payload: dict[str, object]) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(
                        f"{self._api_base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    try:
                        return cast(dict[str, object], response.json())
                    except ValueError as exc:
                        # 非 JSON body 不是瞬时错误，不重试
                        raise ModelProviderError(
                            f"model provider returned invalid json: {exc}"
                        ) from exc
            except httpx.TimeoutException as exc:
                if attempt + 1 == attempts:
                    raise ModelProviderTimeoutError("model provider timeout") from exc
            except httpx.HTTPStatusError as exc:
                # 4xx 是客户端错误，重试无意义
                if exc.response.status_code < 500 or attempt + 1 == attempts:
                    raise ModelProviderError(
                        f"model provider http {exc.response.status_code}: {exc}"
                    ) from exc
            except httpx.HTTPError as exc:
                if attempt + 1 == attempts:
                    raise ModelProviderError(f"model provider http error: {exc}") from exc
            await asyncio.sleep(min(0.05 * (2**attempt), 1.0))
        raise ModelProviderError("model provider failed without response")


def _request_payload(request: ModelRequest, default_model: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": request.model or default_model,
        "messages": [_message_payload(message) for message in request.messages],
    }
    if request.tools:
        payload["tools"] = [_tool_payload(tool) for tool in request.tools]
    return payload


def _message_payload(message: ModelMessage) -> dict[str, object]:
    payload: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [_tool_call_payload(call) for call in message.tool_calls]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        payload["name"] = message.name
    return payload


def _tool_call_payload(call: ToolCall) -> dict[str, object]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(
                call.arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    }


def _tool_payload(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _response_from_openai(provider_id: str, payload: dict[str, object]) -> ModelResponse:
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if not isinstance(message, dict):
        message = {}
    content = message.get("content")
    tool_calls = _tool_calls(message.get("tool_calls"))
    return ModelResponse(
        provider_id=provider_id,
        content=content if isinstance(content, str) else "",
        tool_calls=tool_calls,
    )


def _tool_calls(value: object) -> list[ToolCall]:
    if not isinstance(value, list):
        return []
    calls: list[ToolCall] = []
    for item in value:
        parsed = _tool_call(item)
        if parsed is not None:
            calls.append(parsed)
    return calls


def _tool_call(value: object) -> ToolCall | None:
    if not isinstance(value, dict):
        return None
    function = value.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str):
        return None
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        parsed_args = cast(dict[str, object], arguments)
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = None
        parsed_args = parsed if isinstance(parsed, dict) else {"raw": arguments}
    else:
        parsed_args = {}
    return ToolCall(call_id=str(value.get("id", "")), name=name, arguments=parsed_args)
