"""The single tool boundary: validate, hook, revalidate, authorize, execute, audit."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Protocol

import jsonschema
from muad_contracts import canonical_json
from muad_contracts.canonical import ensure_strict_json
from muad_logging.redaction import redact_value
from pydantic import JsonValue, TypeAdapter

from ..hooks.pipeline import HookEvent, HookPipeline
from .registry import ToolDefinition, ToolNotFoundError, ToolRegistry

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
REDACTED_KEYS = frozenset({"api_key", "secret", "token", "password"})


def compute_args_hash(arguments: Mapping[str, object]) -> str:
    redacted = redact_value(
        {key: value for key, value in arguments.items() if key.lower() not in REDACTED_KEYS}
    )
    return "sha256:" + sha256(canonical_json(redacted).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    args_hash: str


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    status: str
    content: str
    args_hash: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionAudit:
    prepared: PreparedToolCall
    definition: ToolDefinition | None
    status: str
    error_code: str | None
    started_at: datetime
    latency_ms: int


class AuditPort(Protocol):
    async def record(self, event: ToolExecutionAudit) -> None: ...


class ToolExecutionPipeline:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit: AuditPort | None = None,
        policy: Callable[[PreparedToolCall], ToolPolicyDecision] | None = None,
        hooks: HookPipeline | None = None,
    ) -> None:
        self._registry, self._audit, self._policy = registry, audit, policy
        self._hooks = hooks or HookPipeline()

    def prepare(self, *, call_id: str, tool_name: str, arguments: Mapping[str, object]) -> PreparedToolCall:
        data = JSON_OBJECT.validate_python(ensure_strict_json(dict(arguments)))
        return PreparedToolCall(call_id, tool_name, data, compute_args_hash(data))

    async def execute(
        self, prepared: PreparedToolCall, *, on_started: Callable[[str, str], Awaitable[None]] | None = None
    ) -> ToolExecutionResult:
        started_at, started = datetime.now(UTC), monotonic()
        definition: ToolDefinition | None = None
        status, error = "FAILED", None
        try:
            definition = self._registry.get(prepared.tool_name)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            prepared = await self._validate_and_hook(prepared, definition)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            result = self._blocked(prepared, definition)
            if result is None:
                result = await self._invoke(prepared, definition, on_started)
            status, error = result.status, result.error_code
            return result
        except ToolNotFoundError:
            error = "TOOL_NOT_FOUND"
            return self._result(prepared, "NOT_FOUND", f"unknown tool: {prepared.tool_name}", error)
        except (jsonschema.ValidationError, ValueError) as exc:
            error = "SCHEMA_INVALID"
            return self._result(prepared, status, f"invalid arguments: {exc}", error)
        except Exception as exc:
            error = str(getattr(exc, "code", "COMMON_INTERNAL_ERROR"))
            raise
        finally:
            if self._audit is not None:
                await self._audit.record(
                    ToolExecutionAudit(
                        prepared, definition, status, error, started_at, int((monotonic() - started) * 1000)
                    )
                )

    def _blocked(self, call: PreparedToolCall, definition: ToolDefinition) -> ToolExecutionResult | None:
        if self._policy is not None:
            decision = self._policy(call)
            if not decision.allowed:
                error = decision.error_code or "FORBIDDEN"
                content = canonical_json({"error": {"code": error, "message": decision.reason}})
                return self._result(call, "POLICY_DENIED", content, error)
        if definition.handler is None:
            return self._result(call, "FAILED", "no handler registered", "NO_HANDLER")
        return None

    async def _invoke(
        self,
        call: PreparedToolCall,
        definition: ToolDefinition,
        on_started: Callable[[str, str], Awaitable[None]] | None,
    ) -> ToolExecutionResult:
        assert definition.handler is not None
        if on_started is not None:
            await on_started(call.call_id, call.tool_name)
        content = await definition.handler(call.arguments, call_id=call.call_id)
        context = await self._hooks.run(
            HookEvent.POST_TOOL_USE,
            {
                "call_id": call.call_id,
                "tool": call.tool_name,
                "arguments": call.arguments,
                "result": content,
            },
        )
        result = context.payload.get("result", content)
        content = result if isinstance(result, str) else content
        error = _content_error(content)
        return self._result(call, "FAILED" if error else "SUCCEEDED", content, error)

    async def _validate_and_hook(
        self, call: PreparedToolCall, definition: ToolDefinition
    ) -> PreparedToolCall:
        context = await self._hooks.run(
            HookEvent.PRE_TOOL_USE,
            {
                "call_id": call.call_id,
                "tool": call.tool_name,
                "arguments": dict(call.arguments),
            },
        )
        payload = context.payload.get("arguments", call.arguments)
        if not isinstance(payload, Mapping):
            raise ValueError("PRE_TOOL_USE returned invalid arguments")
        final = self.prepare(call_id=call.call_id, tool_name=call.tool_name, arguments=payload)
        return final

    @staticmethod
    def _result(call: PreparedToolCall, status: str, content: str, error: str | None) -> ToolExecutionResult:
        return ToolExecutionResult(call.call_id, call.tool_name, status, content, call.args_hash, error)


def _content_error(content: str) -> str | None:
    import json

    try:
        payload = json.loads(content)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return str(error["code"]) if isinstance(error, dict) and error.get("code") else None
