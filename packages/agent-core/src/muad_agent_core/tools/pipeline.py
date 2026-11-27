"""The single tool boundary: validate, hook, revalidate, authorize, execute, audit."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Protocol
from uuid import UUID

import jsonschema
from muad_contracts import ToolRetrySafety, canonical_json
from muad_contracts.canonical import ensure_strict_json
from muad_logging import get_logger
from muad_logging.redaction import redact_value
from pydantic import JsonValue, TypeAdapter

from ..hooks.pipeline import HookEvent, HookPipeline
from .identity import PreparedCall, ToolCall
from .outcome import (
    ExecutionOutcome,
    RetryDisposition,
    bounded_preview,
    policy_denied_outcome,
    tool_exception_outcome,
)
from .registry import ToolDefinition, ToolEffect, ToolNotFoundError, ToolRegistry
from .result import ToolError, ToolExecutionResult, ToolResult, failure_result

logger = get_logger(__name__)

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
    model_name: str = ""


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""
    error_code: str | None = None


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


#: Runner-owned pre-dispatch gate. It runs after validation/hooks and before policy/handler,
#: so policy denials and blocked attempts share one decision point.
DispatchGate = Callable[[PreparedToolCall, ToolDefinition], ToolExecutionResult | None]


def build_prepared_call(
    prepared: PreparedToolCall, definition: ToolDefinition
) -> PreparedCall:
    """把校验后的调用冻结成外部执行前的持久事实（参数只保留脱敏/有界预览）。"""
    source, skill_id, skill_key, tool_name = _call_source(
        prepared.tool_name, prepared.arguments
    )
    effect = definition.effect
    if source == "MCP" and definition.retry_safety is not ToolRetrySafety.READ_ONLY:
        effect = ToolEffect.EXTERNAL
    targets = (
        definition.write_targets(prepared.arguments)
        if definition.write_targets is not None
        else frozenset()
    )
    if (
        not targets
        and effect is not ToolEffect.READ
        and definition.retry_safety is not ToolRetrySafety.READ_ONLY
    ):
        targets = frozenset({f"tool:{tool_name or prepared.tool_name}"})
    operation_key = (
        definition.write_operation_key(prepared.arguments)
        if definition.write_operation_key is not None
        else None
    )
    return PreparedCall(
        call=ToolCall(
            call_id=prepared.call_id,
            name=prepared.tool_name,
            model_name=prepared.model_name or prepared.tool_name,
            arguments=prepared.arguments,
        ),
        args_hash=prepared.args_hash,
        args_preview=_bounded_args_preview(prepared.arguments),
        source=source,
        skill_id=skill_id,
        skill_key=skill_key,
        tool_name=tool_name,
        execution_mode=definition.execution_mode,
        retry_safety=definition.retry_safety,
        effect=effect.value,
        write_targets=frozenset(target for target in targets if isinstance(target, str) and target),
        write_operation_key=operation_key if isinstance(operation_key, str) and operation_key else None,
    )


def _bounded_args_preview(
    arguments: Mapping[str, object], *, limit_bytes: int = 2048
) -> Mapping[str, object]:
    redacted = redact_value(dict(arguments))
    encoded = canonical_json(redacted)
    if len(encoded.encode("utf-8")) <= limit_bytes:
        return redacted if isinstance(redacted, Mapping) else {}
    return {
        "_truncated": True,
        "preview": bounded_preview(encoded, limit_bytes=limit_bytes),
    }


def _call_source(
    name: str, arguments: Mapping[str, object]
) -> tuple[str, UUID | None, str | None, str | None]:
    """从已冻结的工具名/参数投影来源；恢复只读这些事实，不重新解析当前配置。"""
    if name.startswith("mcp::"):
        parts = name.split("::", 2)
        return "MCP", None, None, parts[2] if len(parts) == 3 else None
    if name.startswith("skill::"):
        parts = name.split("::", 2)
        skill_id = None
        if len(parts) == 3:
            try:
                skill_id = UUID(parts[1])
            except ValueError:
                skill_id = None
        return "SKILL", skill_id, None, parts[2] if len(parts) == 3 else None
    if name == "execute" and arguments.get("target") == "skill":
        skill_key = arguments.get("skill_key")
        tool = arguments.get("tool")
        return (
            "SKILL",
            None,
            skill_key if isinstance(skill_key, str) else None,
            tool if isinstance(tool, str) else None,
        )
    return "PLATFORM", None, None, None


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

    def prepare(
        self, *, call_id: str, tool_name: str, arguments: Mapping[str, object]
    ) -> PreparedToolCall:
        data = JSON_OBJECT.validate_python(ensure_strict_json(dict(arguments)))
        return PreparedToolCall(
            call_id=call_id,
            tool_name=tool_name,
            arguments=data,
            args_hash=compute_args_hash(data),
            model_name=self._registry.model_name_for(tool_name),
        )

    async def execute(self, prepared: PreparedToolCall) -> ToolExecutionResult:
        outcome = await self.prepare_execution(prepared)
        if isinstance(outcome, ToolExecutionResult):
            return outcome
        return await self.run_prepared(outcome)

    async def prepare_execution(
        self, prepared: PreparedToolCall, *, gate: DispatchGate | None = None
    ) -> RunnableToolCall | ToolExecutionResult:
        """校验/授权（不含 handler IO）；未通过时返回终态结果并写审计。

        并发编排用：按原调用序先跑完这一段，TaskGroup 里只放 `run_prepared` 的 handler IO。
        `gate` 在 policy 之前执行，保证 policy deny 不会绕过 blocked attempt / unknown write。
        """
        started_at, started = datetime.now(UTC), monotonic()
        if self._audit is not None:
            self._audit.prepare(prepared)
        definition: ToolDefinition | None = None
        execution: ToolExecutionResult | None = None
        try:
            definition = self._registry.get(prepared.tool_name)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            prepared = await self._validate_and_hook(prepared, definition)
            if self._audit is not None:
                self._audit.prepare(prepared)
            jsonschema.validate(instance=prepared.arguments, schema=definition.input_schema)
            execution = gate(prepared, definition) if gate is not None else None
            if execution is None:
                execution = self._blocked(prepared, definition)
        except ToolNotFoundError:
            execution = self._failure(
                prepared,
                ToolError(
                    "TOOL_NOT_FOUND",
                    f"unknown tool: {prepared.tool_name}",
                    retryable=True,
                ),
                status="NOT_FOUND",
            )
        except jsonschema.ValidationError as exc:
            # 实例可能含私有输入：只回字段与期望，不回显任何值。
            execution = self._failure(
                prepared, ToolError("SCHEMA_INVALID", schema_problem(exc), retryable=True)
            )
        except ToolError as exc:
            execution = failure_result(self._call(prepared), exc)
        except ValueError:
            execution = self._failure(
                prepared,
                ToolError("SCHEMA_INVALID", "invalid tool arguments", retryable=True),
            )
        except Exception as exc:
            error = str(getattr(exc, "code", "COMMON_INTERNAL_ERROR"))
            await self._record_audit(
                prepared,
                definition,
                "FAILED",
                error,
                started_at,
                started,
                tool_exception_outcome(
                    code=error,
                    message=str(exc),
                    retryable=getattr(exc, "retryable", None),
                    retry_after=getattr(exc, "retry_after", None),
                ),
            )
            raise
        if execution is not None:
            await self._record_audit(
                prepared,
                definition,
                execution.status,
                execution.result.outcome.error_code,
                started_at,
                started,
                execution.result.outcome,
            )
            return execution
        assert definition is not None  # 校验通过 ⇒ registry.get 已成功
        return RunnableToolCall(prepared, definition)

    async def run_prepared(self, runnable: RunnableToolCall) -> ToolExecutionResult:
        """handler IO + POST hook（可并发调用）；审计在本方法内完成。"""
        started_at, started = datetime.now(UTC), monotonic()
        try:
            execution = await self._invoke(runnable.prepared, runnable.definition)
        except ToolError as exc:
            execution = failure_result(self._call(runnable.prepared), exc)
            await self._record_audit(
                runnable.prepared,
                runnable.definition,
                execution.status,
                exc.code,
                started_at,
                started,
                execution.result.outcome,
            )
            return execution
        except Exception as exc:
            error = str(getattr(exc, "code", "COMMON_INTERNAL_ERROR"))
            await self._record_audit(
                runnable.prepared,
                runnable.definition,
                "FAILED",
                error,
                started_at,
                started,
                tool_exception_outcome(
                    code=error,
                    message=str(exc),
                    retryable=getattr(exc, "retryable", None),
                    sent=getattr(exc, "sent", False) is True,
                    outcome_known=getattr(exc, "outcome_known", False) is True,
                ),
            )
            raise
        await self._record_audit(
            runnable.prepared,
            runnable.definition,
            execution.status,
            execution.result.outcome.error_code,
            started_at,
            started,
            execution.result.outcome,
        )
        return execution

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

    def _blocked(
        self, call: PreparedToolCall, definition: ToolDefinition
    ) -> ToolExecutionResult | None:
        if self._policy is not None:
            decision = self._policy(call)
            if not decision.allowed:
                error = decision.error_code or "FORBIDDEN"
                identity = self._call(call)
                result = ToolResult(
                    content=ToolError(error, decision.reason).content(),
                    outcome=policy_denied_outcome(error, decision.reason),
                ).with_identity(identity)
                return ToolExecutionResult(identity, result, "POLICY_DENIED")
        if definition.handler is None:
            return self._failure(call, ToolError("NO_HANDLER", "no handler registered"))
        return None

    async def _invoke(
        self,
        call: PreparedToolCall,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        assert definition.handler is not None
        identity = self._call(call)
        result = await definition.handler(identity.arguments, call=identity)
        if not isinstance(result, ToolResult):
            raise TypeError(
                f"tool {definition.name!r} returned {type(result).__name__}, expected ToolResult"
            )
        try:
            context = await self._hooks.run(
                HookEvent.POST_TOOL_USE,
                {
                    "call_id": call.call_id,
                    "tool": call.tool_name,
                    "arguments": call.arguments,
                    "result": result.content,
                },
            )
        except Exception as exc:
            control = result.outcome.control
            error = ToolError(
                "TOOL_POST_PROCESSING_FAILED",
                "The tool ran, but its result could not be processed. Verify it before running it again.",
                retryable=False,
                sent=control.sent,
                outcome_known=control.outcome_known,
            )
            failure = failure_result(identity, error)
            outcome = replace(
                failure.result.outcome,
                control=replace(control, retry_disposition=RetryDisposition.BLOCK),
            )
            preserved = replace(
                failure.result,
                outcome=outcome,
                artifacts=result.artifacts,
                follow_ups=result.follow_ups,
            )
            logger.warning("post_tool_hook_failed", extra={"error_type": type(exc).__name__})
            return replace(failure, result=preserved)
        updated = context.payload.get("result", result.content)
        if isinstance(updated, str) and updated != result.content:
            result = replace(result, content=updated)
        result = replace(result, externalizable=definition.externalizable_result)
        return ToolExecutionResult(identity, result.with_identity(identity), "SUCCEEDED")

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
            raise ToolError("SCHEMA_INVALID", "PRE_TOOL_USE returned invalid arguments")
        final = self.prepare(call_id=call.call_id, tool_name=call.tool_name, arguments=payload)
        return replace(final, model_name=call.model_name)

    @staticmethod
    def _call(prepared: PreparedToolCall) -> ToolCall:
        return ToolCall(
            call_id=prepared.call_id,
            name=prepared.tool_name,
            model_name=prepared.model_name or prepared.tool_name,
            arguments=prepared.arguments,
        )

    def _failure(
        self, prepared: PreparedToolCall, error: ToolError, *, status: str = "FAILED"
    ) -> ToolExecutionResult:
        return failure_result(self._call(prepared), error, status=status)
