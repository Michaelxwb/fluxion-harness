"""ToolRegistry prepare/execute 统一链：schema 校验→policy→execute→审计 port。"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .registry import ToolRegistry

REDACTED_KEYS = frozenset({"api_key", "secret", "token", "password"})


def compute_args_hash(arguments: dict[str, Any]) -> str:
    """prepared_args_hash：脱敏后稳定计算（敏感值替换为占位符）。"""
    redacted = _redact(arguments)
    canonical = json.dumps(redacted, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key.lower() not in REDACTED_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    args_hash: str


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    status: str  # SUCCEEDED / FAILED / POLICY_DENIED
    content: str
    args_hash: str
    error_code: str | None = None


class ToolPolicy(Protocol):
    def __call__(self, call: PreparedToolCall) -> ToolPolicyDecision: ...


class AuditPort(Protocol):
    # 允许同步或异步实现：调用方用 inspect.isawaitable 判定，故返回类型为 Any
    def record(self, event: dict[str, Any]) -> Any: ...


class ToolExecutionPipeline:
    """schema 校验→policy→execute→post 审计；失败路径同样落终态；异常不吞。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit: AuditPort,
        policy: Callable[[PreparedToolCall], ToolPolicyDecision] | None = None,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._policy = policy

    def prepare(
        self, *, call_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> PreparedToolCall:
        return PreparedToolCall(
            call_id=call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            args_hash=compute_args_hash(arguments),
        )

    async def execute(self, prepared: PreparedToolCall) -> ToolExecutionResult:
        if self._policy is not None:
            decision = self._policy(prepared)
            if not decision.allowed:
                await self._audit_failed(prepared, "POLICY_DENIED", decision.reason)
                return ToolExecutionResult(
                    call_id=prepared.call_id,
                    tool_name=prepared.tool_name,
                    status="POLICY_DENIED",
                    content=decision.reason,
                    args_hash=prepared.args_hash,
                )

        definition = self._registry.get(prepared.tool_name)
        error = self._validate_schema(definition.input_schema, prepared.arguments)
        if error is not None:
            await self._audit_failed(prepared, "SCHEMA_INVALID", error)
            return ToolExecutionResult(
                call_id=prepared.call_id,
                tool_name=prepared.tool_name,
                status="FAILED",
                content=f"invalid arguments: {error}",
                args_hash=prepared.args_hash,
                error_code="SCHEMA_INVALID",
            )

        if definition.handler is None:
            await self._audit_failed(prepared, "NO_HANDLER", "no handler registered")
            return ToolExecutionResult(
                call_id=prepared.call_id,
                tool_name=prepared.tool_name,
                status="FAILED",
                content="no handler registered",
                args_hash=prepared.args_hash,
                error_code="NO_HANDLER",
            )

        content: str
        try:
            outcome = definition.handler(prepared.arguments)
            # ToolHandler 协议声明为 async，但运行时同时容忍同步 handler：
            # 返回 awaitable 就 await，否则直接取返回值（不做强制转换）
            resolved: Any = await outcome if inspect.isawaitable(outcome) else outcome
            content = cast(str, resolved)
        except Exception as exc:
            # 失败路径同样落终态审计，然后异常继续传播（不吞）
            await self._emit(
                {
                    "call_id": prepared.call_id,
                    "tool_name": prepared.tool_name,
                    "prepared_args_hash": prepared.args_hash,
                    "status": "FAILED",
                    "error_code": type(exc).__name__,
                }
            )
            raise
        await self._emit(
            {
                "call_id": prepared.call_id,
                "tool_name": prepared.tool_name,
                "prepared_args_hash": prepared.args_hash,
                "status": "SUCCEEDED",
                "error_code": None,
            }
        )
        return ToolExecutionResult(
            call_id=prepared.call_id,
            tool_name=prepared.tool_name,
            status="SUCCEEDED",
            content=content,
            args_hash=prepared.args_hash,
        )

    async def _audit_failed(
        self, prepared: PreparedToolCall, status: str, reason: str
    ) -> None:
        await self._emit(
            {
                "call_id": prepared.call_id,
                "tool_name": prepared.tool_name,
                "prepared_args_hash": prepared.args_hash,
                "status": status,
                "error": reason,
            }
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        outcome = self._audit.record(event)
        if inspect.isawaitable(outcome):
            await outcome

    @staticmethod
    def _validate_schema(
        schema: Mapping[str, Any], arguments: dict[str, Any]
    ) -> str | None:
        """轻量 JSON-schema 校验：required + 顶层 type/properties 类型。"""
        import jsonschema

        try:
            jsonschema.validate(instance=arguments, schema=schema)
        except jsonschema.ValidationError as exc:
            return exc.message
        return None
