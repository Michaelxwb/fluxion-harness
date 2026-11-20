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
from .outcome import (
    BusinessStatus,
    ExecutionOutcome,
    FailureLayer,
    diagnostic_evidence_outcome,
    policy_denied_outcome,
    tool_exception_outcome,
    tool_failure_outcome,
)
from .registry import ToolDefinition, ToolNotFoundError, ToolRegistry

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
REDACTED_KEYS = frozenset({"api_key", "secret", "token", "password"})


def schema_problem(error: jsonschema.ValidationError) -> str:
    """把 schema 校验失败变成可归因的说明：只报字段与期望，**绝不回显实例值**。

    入参可能含凭据，所以 `error.message` 里夹带的实例内容一律不用；字段名从
    `error.absolute_path` 与 `error.instance` 的**键**里取，允许集从 `error.schema` 里取。
    """
    where = ".".join(str(part) for part in error.absolute_path) or "arguments"
    schema = error.schema if isinstance(error.schema, Mapping) else {}
    instance = error.instance
    if error.validator == "required" and isinstance(instance, Mapping):
        missing = [key for key in schema.get("required") or () if key not in instance]
        if missing:
            return "invalid tool arguments: missing required field(s) " + ", ".join(missing)
    if error.validator == "additionalProperties" and isinstance(instance, Mapping):
        allowed = [str(key) for key in (schema.get("properties") or {})]
        unknown = sorted(str(key) for key in instance if str(key) not in set(allowed))
        if unknown:
            return (
                "invalid tool arguments: unexpected field(s) "
                + ", ".join(unknown)
                + "; allowed: "
                + ", ".join(sorted(allowed))
            )
    if error.validator == "type":
        return f"invalid tool arguments: {where} must be {error.validator_value}"
    if error.validator == "enum":
        enum_values = error.validator_value
        choices = (
            ", ".join(str(item) for item in enum_values)
            if isinstance(enum_values, (list, tuple))
            else str(enum_values)
        )
        return f"invalid tool arguments: {where} must be one of {choices}"
    if error.validator in {"minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"}:
        op = {
            "minimum": ">=",
            "exclusiveMinimum": ">",
            "maximum": "<=",
            "exclusiveMaximum": "<",
        }[error.validator]
        return f"invalid tool arguments: {where} must be {op} {error.validator_value}"
    if error.validator in {"minItems", "maxItems"}:
        op = "at least" if error.validator == "minItems" else "at most"
        return f"invalid tool arguments: {where} must have {op} {error.validator_value} item(s)"
    return f"invalid tool arguments: {where} failed {error.validator}"


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
    #: 业务执行判定（design §4.2）；非业务执行工具为 `NOT_APPLICABLE`。
    outcome: ExecutionOutcome | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionAudit:
    prepared: PreparedToolCall
    definition: ToolDefinition | None
    status: str
    error_code: str | None
    started_at: datetime
    latency_ms: int
    outcome: ExecutionOutcome | None = None


class AuditPort(Protocol):
    def prepare(self, call: PreparedToolCall) -> None: ...

    async def record(self, event: ToolExecutionAudit) -> None: ...


@dataclass(frozen=True, slots=True)
class RunnableToolCall:
    """校验/授权通过、可以进入 handler IO 的调用（并发批只包住这一段）。"""

    prepared: PreparedToolCall
    definition: ToolDefinition


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
        outcome = await self.prepare_execution(prepared)
        if isinstance(outcome, ToolExecutionResult):
            return outcome
        return await self.run_prepared(outcome, on_started=on_started)

    async def prepare_execution(self, prepared: PreparedToolCall) -> RunnableToolCall | ToolExecutionResult:
        """校验/授权（不含 handler IO）；未通过时返回终态结果并写审计。

        并发编排用：按原调用序先跑完这一段，TaskGroup 里只放 `run_prepared` 的 handler IO。
        """
        started_at, started = datetime.now(UTC), monotonic()
        if self._audit is not None:
            self._audit.prepare(prepared)
        definition: ToolDefinition | None = None
        try:
            definition = self._registry.get(prepared.tool_name)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            prepared = await self._validate_and_hook(prepared, definition)
            if self._audit is not None:
                self._audit.prepare(prepared)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            result = self._blocked(prepared, definition)
        except ToolNotFoundError:
            result = self._result(
                prepared, "NOT_FOUND", f"unknown tool: {prepared.tool_name}", "TOOL_NOT_FOUND"
            )
        except jsonschema.ValidationError as exc:
            # 实例可能含私有输入：只回字段与期望，不回显任何值。
            result = self._result(prepared, "FAILED", schema_problem(exc), "SCHEMA_INVALID")
        except ValueError:
            result = self._result(prepared, "FAILED", "invalid tool arguments", "SCHEMA_INVALID")
        except Exception as exc:
            error = str(getattr(exc, "code", "COMMON_INTERNAL_ERROR"))
            await self._record_audit(prepared, definition, "FAILED", error, started_at, started, None)
            raise
        if result is not None:
            await self._record_audit(
                prepared,
                definition,
                result.status,
                result.error_code,
                started_at,
                started,
                result.outcome,
            )
            return result
        assert definition is not None  # 校验通过 ⇒ registry.get 已成功
        return RunnableToolCall(prepared, definition)

    async def run_prepared(
        self,
        runnable: RunnableToolCall,
        *,
        on_started: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> ToolExecutionResult:
        """handler IO + POST hook（可并发调用）；审计在本方法内完成。"""
        started_at, started = datetime.now(UTC), monotonic()
        status, error, outcome = "FAILED", None, None
        try:
            result = await self._invoke(runnable.prepared, runnable.definition, on_started)
            status, error, outcome = result.status, result.error_code, result.outcome
            return result
        except Exception as exc:
            error = str(getattr(exc, "code", "COMMON_INTERNAL_ERROR"))
            outcome = tool_exception_outcome(
                code=error,
                message=str(exc),
                retryable=getattr(exc, "retryable", None),
            )
            raise
        finally:
            await self._record_audit(
                runnable.prepared,
                runnable.definition,
                status,
                error,
                started_at,
                started,
                outcome,
            )

    async def _record_audit(
        self,
        prepared: PreparedToolCall,
        definition: ToolDefinition | None,
        status: str,
        error: str | None,
        started_at: datetime,
        started: float,
        outcome: ExecutionOutcome | None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            ToolExecutionAudit(
                prepared,
                definition,
                status,
                error,
                started_at,
                int((monotonic() - started) * 1000),
                outcome=outcome,
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
        outcome = self._outcome(definition, content)
        error = (
            outcome.error_code
            if outcome is not None
            and outcome.is_failure
            and outcome.failure_layer is FailureLayer.TOOL
            else None
        )
        return self._result(
            call,
            "FAILED" if error else "SUCCEEDED",
            content,
            error,
            outcome,
        )

    @staticmethod
    def _outcome(definition: ToolDefinition, content: str) -> ExecutionOutcome:
        """判定一次业务执行（只调用一次解析器）；解析器异常降级成 TOOL 失败。"""
        tool_error = tool_failure_outcome(content)
        if definition.outcome_parser is None:
            return tool_error or diagnostic_evidence_outcome(content)
        try:
            parsed = definition.outcome_parser(content)
        except Exception as exc:  # noqa: BLE001 —— 解析器不得让 Run 崩在判定上
            return tool_exception_outcome(
                code="COMMON_INTERNAL_ERROR",
                message=f"outcome parser failed: {type(exc).__name__}",
            )
        if parsed is None:
            return tool_error or diagnostic_evidence_outcome(content)
        return parsed

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
    def _result(
        call: PreparedToolCall,
        status: str,
        content: str,
        error: str | None,
        outcome: ExecutionOutcome | None = None,
    ) -> ToolExecutionResult:
        if outcome is None:
            if status == "POLICY_DENIED":
                outcome = policy_denied_outcome(
                    error or "FORBIDDEN", _error_message(content) or content
                )
            elif status in ("FAILED", "NOT_FOUND"):
                outcome = tool_exception_outcome(
                    code=error,
                    message=_error_message(content) or content,
                )
                outcome = ExecutionOutcome(
                    transport_status="ERROR",
                    business_status=outcome.business_status,
                    failure_layer=outcome.failure_layer,
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                    retryable=outcome.retryable,
                    evidence=outcome.evidence,
                )
            else:
                outcome = ExecutionOutcome(business_status=BusinessStatus.NOT_APPLICABLE)
        resolved = outcome.with_identity(
            call_id=call.call_id,
            tool_name=call.tool_name,
            transport_status={
                "POLICY_DENIED": "BLOCKED",
                "NOT_FOUND": "NOT_FOUND",
                "FAILED": "ERROR",
                "SUCCEEDED": "OK",
            }.get(status, outcome.transport_status),
        )
        return ToolExecutionResult(
            call.call_id, call.tool_name, status, content, call.args_hash, error, resolved
        )


def _error_message(content: str) -> str:
    import json

    try:
        payload = json.loads(content)
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        return message if isinstance(message, str) else ""
    return error if isinstance(error, str) else ""
