from __future__ import annotations

import json
from typing import Any

import httpx
from muad_platform_sdk.types import SecretValue

from ..tools.registry import ToolDefinition
from .errors import ModelRateLimitedError, ModelRequestError, ModelUnavailableError
from .provider import ModelMessage, ModelRequest, ModelResponse, ModelToolCall

CHAT_COMPLETIONS_PATH = "/chat/completions"
RATE_LIMIT_STATUS = 429
SERVER_ERROR_STATUS = 500
DEFAULT_TIMEOUT_SEC = 60.0


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: SecretValue | str | None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key.value if isinstance(api_key, SecretValue) else api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_sec, transport=transport)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self._post(request)
        return self._parse(response)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, request: ModelRequest) -> httpx.Response:
        try:
            response = await self._client.post(
                f"{self._base_url}{CHAT_COMPLETIONS_PATH}",
                json=self._payload(request),
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ModelUnavailableError("model request timed out") from exc
        except httpx.TransportError as exc:
            raise ModelUnavailableError(f"model transport error: {type(exc).__name__}") from exc
        if response.status_code == RATE_LIMIT_STATUS:
            raise ModelRateLimitedError("model rate limited", retry_after=self._retry_after(response))
        if response.status_code >= SERVER_ERROR_STATUS:
            raise ModelUnavailableError(f"model server error: {response.status_code}")
        if response.status_code >= 400:
            raise ModelRequestError(f"model request rejected: {response.status_code}")
        return response

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message(item) for item in request.messages],
        }
        payload.update(request.params)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [self._tool(definition) for definition in request.tools]
        return payload

    @staticmethod
    def _message(message: ModelMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": str(message.role), "content": message.content}
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    @staticmethod
    def _tool(definition: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": dict(definition.input_schema),
            },
        }

    def _parse(self, response: httpx.Response) -> ModelResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelRequestError("model response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ModelRequestError("model response payload is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelRequestError("model response has no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelRequestError("model response choice is not an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelRequestError("model response has no message")
        content = message.get("content")
        finish_reason = choice.get("finish_reason")
        input_tokens, output_tokens = self._usage(payload.get("usage"))
        return ModelResponse(
            content=content if isinstance(content, str) else "",
            finish_reason=finish_reason if isinstance(finish_reason, str) else "stop",
            tool_calls=self._tool_calls(message.get("tool_calls")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _tool_calls(raw: Any) -> tuple[ModelToolCall, ...]:
        if not isinstance(raw, list):
            return ()
        calls: list[ModelToolCall] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ModelRequestError("model tool call is not an object")
            function = item.get("function")
            if not isinstance(function, dict):
                raise ModelRequestError("model tool call has no function")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ModelRequestError("model tool call has no name")
            call_id = item.get("id")
            calls.append(
                ModelToolCall(
                    id=call_id if isinstance(call_id, str) and call_id else f"call-{index}",
                    name=name,
                    arguments=OpenAICompatibleProvider._arguments(function.get("arguments")),
                )
            )
        return tuple(calls)

    @staticmethod
    def _arguments(raw: Any) -> dict[str, Any]:
        if raw is None or raw == "":
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if not isinstance(raw, str):
            raise ModelRequestError("model tool call arguments are not a string")
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ModelRequestError("model tool call arguments are not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ModelRequestError("model tool call arguments are not an object")
        return parsed

    @staticmethod
    def _usage(raw: Any) -> tuple[int | None, int | None]:
        if not isinstance(raw, dict):
            return None, None
        input_tokens = raw.get("prompt_tokens")
        output_tokens = raw.get("completion_tokens")
        return (
            input_tokens if isinstance(input_tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value >= 0 else None
