"""Streamable HTTP MCP 客户端：initialize + tools/list（auth_secret 不落日志）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

_JSON_RPC_VERSION = "2.0"
_PROTOCOL_VERSION = "2025-03-26"


class McpClientError(Exception):
    """MCP 传输/协议失败。reason: connect | timeout | protocol。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"MCP {reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: str


def normalize_effect(raw_tool: dict[str, Any]) -> str:
    annotations = raw_tool.get("annotations") or {}
    if annotations.get("readOnlyHint") is True:
        return "READ"
    if annotations.get("destructiveHint") is True:
        return "DESTRUCTIVE"
    return "WRITE"


def normalize_tools(tools: list[McpToolSpec]) -> list[dict[str, Any]]:
    """转为 tool_catalog_json 条目（稳定键序保证 hash 可复现）。"""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "effect": tool.effect,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    ]


def catalog_hash(catalog: list[dict[str, Any]]) -> str:
    payload = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class McpClient:
    def __init__(
        self,
        endpoint: str,
        *,
        auth_secret: str | None = None,
        timeout_ms: int = 5000,
    ) -> None:
        headers = {"Accept": "application/json, text/event-stream"}
        if auth_secret:
            headers["Authorization"] = f"Bearer {auth_secret}"
        self._endpoint = endpoint
        self._client = httpx.Client(headers=headers, timeout=httpx.Timeout(timeout_ms / 1000))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(self._endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise McpClientError("timeout", "request timed out") from exc
        except httpx.HTTPError as exc:
            # 不携带请求头（含 auth）内容，避免 Secret 泄漏
            raise McpClientError("connect", type(exc).__name__) from exc
        if response.status_code >= 400:
            raise McpClientError("protocol", f"http {response.status_code}")
        if response.status_code == 202 or not response.content:
            return {}
        try:
            body = self._decode(response)
        except (json.JSONDecodeError, ValueError) as exc:
            raise McpClientError("protocol", "invalid json payload") from exc
        if body.get("error") is not None:
            raise McpClientError("protocol", str(body["error"].get("message", "rpc error")))
        return body

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:") :].strip())
            raise McpClientError("protocol", "empty sse payload")
        return response.json()

    def initialize(self) -> dict[str, Any]:
        body = self._post(
            {
                "jsonrpc": _JSON_RPC_VERSION,
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "muad-console-platform", "version": "1.0.0"},
                },
            }
        )
        result = body.get("result") or {}
        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict):
            raise McpClientError("protocol", "initialize missing serverInfo")
        self._post({"jsonrpc": _JSON_RPC_VERSION, "method": "notifications/initialized"})
        return server_info

    def list_tools(self) -> list[McpToolSpec]:
        body = self._post({"jsonrpc": _JSON_RPC_VERSION, "id": 2, "method": "tools/list"})
        tools = (body.get("result") or {}).get("tools") or []
        specs: list[McpToolSpec] = []
        for raw in tools:
            if not isinstance(raw, dict) or not raw.get("name"):
                raise McpClientError("protocol", "tool entry missing name")
            specs.append(
                McpToolSpec(
                    name=str(raw["name"]),
                    description=str(raw.get("description") or ""),
                    input_schema=raw.get("inputSchema") or {"type": "object", "properties": {}},
                    effect=normalize_effect(raw),
                )
            )
        return specs
