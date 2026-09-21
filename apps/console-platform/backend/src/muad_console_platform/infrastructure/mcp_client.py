"""Streamable HTTP MCP 客户端：initialize + tools/list（auth_secret 不落日志）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import httpx

_JSON_RPC_VERSION = "2.0"
_PROTOCOL_VERSION = "2025-03-26"
_MAX_PAGES = 50


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
        self._endpoint = endpoint
        self._headers = {"Accept": "application/json, text/event-stream"}
        if auth_secret:
            self._headers["Authorization"] = f"Bearer {auth_secret}"
        self._client = httpx.Client(headers=self._headers, timeout=httpx.Timeout(timeout_ms / 1000))
        self._session_id: str | None = None

    def close(self) -> None:
        self._client.close()

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        expects_response = payload.get("id") is not None
        try:
            response = self._client.post(
                self._endpoint, json=payload, headers=self._request_headers()
            )
        except httpx.TimeoutException as exc:
            raise McpClientError("timeout", "request timed out") from exc
        except httpx.HTTPError as exc:
            # 不携带请求头（含 auth）内容，避免 Secret 泄漏
            raise McpClientError("connect", type(exc).__name__) from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if response.status_code >= 400:
            raise McpClientError("protocol", f"http {response.status_code}")
        if response.status_code == 202 or not response.content:
            if expects_response:
                raise McpClientError("protocol", "missing response payload")
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
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[len("data:") :].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                    return cast(dict[str, Any], payload)
            raise McpClientError("protocol", "empty sse payload")
        return cast(dict[str, Any], response.json())

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

    def list_tools(self, *, max_tools: int | None = None) -> list[McpToolSpec]:
        """按 cursor 分页拉取全部工具；超过 max_tools 立即失败（避免无界拉取）。"""
        specs: list[McpToolSpec] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            payload: dict[str, Any] = {
                "jsonrpc": _JSON_RPC_VERSION,
                "id": 2,
                "method": "tools/list",
            }
            if params:
                payload["params"] = params
            body = self._post(payload)
            result = body.get("result") or {}
            tools = result.get("tools") or []
            if not isinstance(tools, list):
                raise McpClientError("protocol", "tools/list result is not a list")
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
                if max_tools is not None and len(specs) > max_tools:
                    raise McpClientError("protocol", f"tool count exceeds limit {max_tools}")
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                return specs
            cursor = str(next_cursor)
        raise McpClientError("protocol", "tools/list pagination did not terminate")
