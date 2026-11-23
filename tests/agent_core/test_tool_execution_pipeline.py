"""[B-113] ToolRegistry prepare/execute 统一链：schema 校验→policy→execute→审计 port。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from muad_agent_core.tools import ToolCall, ToolDefinition, ToolEffect, ToolRegistry, ToolResult
from muad_agent_core.tools.pipeline import (
    PreparedToolCall,
    ToolExecutionPipeline,
    ToolPolicyDecision,
    compute_args_hash,
)

CALLS: list[dict[str, Any]] = []


async def _echo_handler(arguments: dict[str, Any], *, call: ToolCall) -> ToolResult:
    CALLS.append(dict(arguments))
    return ToolResult(json.dumps({"echo": arguments.get("text")}))


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}, "api_key": {"type": "string"}},
                "required": ["text"],
            },
            effect=ToolEffect.READ,
            handler=_echo_handler,
        )
    )
    return registry


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def prepare(self, call: PreparedToolCall) -> None:
        assert call.args_hash.startswith("sha256:")

    async def record(self, event: dict[str, Any]) -> None:
        self.events.append(
            {
                "status": event.status,
                "prepared_args_hash": event.prepared.args_hash,
                "error_code": event.error_code,
            }
        )


def _pipeline(audit: RecordingAudit, policy=None) -> ToolExecutionPipeline:
    return ToolExecutionPipeline(registry=_registry(), audit=audit, policy=policy)


async def test_b113_happy_path_schema_then_handler_then_audit() -> None:
    """[B-113] schema 校验→handler→审计成功终态。"""
    audit = RecordingAudit()
    pipeline = _pipeline(audit)
    prepared = pipeline.prepare(call_id="c1", tool_name="echo", arguments={"text": "ping"})
    assert isinstance(prepared, PreparedToolCall)
    assert prepared.arguments == {"text": "ping"}
    assert prepared.args_hash.startswith("sha256:")

    result = await pipeline.execute(prepared)
    assert result.status == "SUCCEEDED"
    assert CALLS == [{"text": "ping"}]
    assert len(audit.events) == 1
    assert audit.events[0]["status"] == "SUCCEEDED"
    assert audit.events[0]["prepared_args_hash"] == prepared.args_hash


async def test_b113_invalid_schema_never_calls_handler() -> None:
    """[B-113] 错误 schema：handler 不调用，审计失败终态。"""
    audit = RecordingAudit()
    pipeline = _pipeline(audit)
    prepared = pipeline.prepare(call_id="c2", tool_name="echo", arguments={"wrong": 1})
    result = await pipeline.execute(prepared)
    assert result.status == "FAILED"
    assert CALLS == []  # handler 未调用
    assert len(audit.events) == 1
    assert audit.events[0]["status"] in ("FAILED", "SCHEMA_INVALID")


async def test_b113_policy_deny_never_calls_handler() -> None:
    """[B-113] 拒绝策略：handler 不调用，审计 POLICY_DENIED。"""

    def deny(call: PreparedToolCall) -> ToolPolicyDecision:
        return ToolPolicyDecision(allowed=False, reason="egress denied")

    audit = RecordingAudit()
    pipeline = _pipeline(audit, policy=deny)
    prepared = pipeline.prepare(call_id="c3", tool_name="echo", arguments={"text": "x"})
    result = await pipeline.execute(prepared)
    assert result.status == "POLICY_DENIED"
    assert CALLS == []


async def test_b113_args_hash_stable_and_redacted() -> None:
    """[B-113] prepared_args_hash 稳定且脱敏：api_key 值不改变 hash。"""
    pipeline = _pipeline(RecordingAudit())
    hash_plain = pipeline.prepare(call_id="c4", tool_name="echo", arguments={"text": "same"}).args_hash
    hash_with_key = pipeline.prepare(
        call_id="c5", tool_name="echo", arguments={"text": "same", "api_key": "sk-very-secret"}
    ).args_hash
    # hash 稳定（同参数同 hash），脱敏字段不改变 hash
    assert hash_plain.startswith("sha256:")
    assert hash_plain == hash_with_key


async def test_b113_handler_failure_audits_and_reraises() -> None:
    """[B-113] handler 异常：审计失败终态后异常向上传播（不吞）。"""

    async def bad_handler(arguments: dict[str, Any], *, call: ToolCall) -> ToolResult:
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="bad",
            description="bad",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=bad_handler,
        )
    )
    audit = RecordingAudit()
    pipeline = ToolExecutionPipeline(registry=registry, audit=audit)
    prepared = pipeline.prepare(call_id="c6", tool_name="bad", arguments={})
    with pytest.raises(RuntimeError, match="boom"):
        await pipeline.execute(prepared)
    assert len(audit.events) == 1
    assert audit.events[0]["status"] == "FAILED"  # 失败终态先落审计


@pytest.mark.parametrize("rewritten", [{"text": 4}, {"text": "changed"}])
async def test_final_hook_parameters_are_revalidated_before_authorization(rewritten):
    from muad_agent_core.hooks import HookContext, HookEvent, HookPipeline

    hooks, authorized, audit = HookPipeline(), [], RecordingAudit()

    async def rewrite(context):
        return HookContext(context.event, {**context.payload, "arguments": rewritten})

    def policy(call):
        authorized.append(call.arguments)
        return ToolPolicyDecision(False, "deny changed arguments")

    hooks.register(HookEvent.PRE_TOOL_USE, rewrite)
    pipeline = ToolExecutionPipeline(registry=_registry(), audit=audit, hooks=hooks, policy=policy)
    result = await pipeline.execute(
        pipeline.prepare(call_id="rewritten", tool_name="echo", arguments={"text": "original"})
    )
    assert CALLS == [] and len(audit.events) == 1
    if isinstance(rewritten["text"], str):
        assert authorized == [rewritten] and result.status == "POLICY_DENIED"
    else:
        assert authorized == [] and result.result.outcome.error_code == "SCHEMA_INVALID"


async def test_prepared_hash_comes_from_the_hook_rewritten_arguments() -> None:
    """执行参数指纹只由 prepare 归一化后的 B 决定，恢复不再重算 assistant 的 A。"""
    from muad_agent_core.hooks import HookContext, HookEvent, HookPipeline

    hooks = HookPipeline()

    async def rewrite(context):
        return HookContext(context.event, {**context.payload, "arguments": {"text": "changed"}})

    hooks.register(HookEvent.PRE_TOOL_USE, rewrite)
    audit = RecordingAudit()
    pipeline = ToolExecutionPipeline(registry=_registry(), audit=audit, hooks=hooks)
    prepared = pipeline.prepare(
        call_id="rewritten-ok", tool_name="echo", arguments={"text": "original"}
    )

    result = await pipeline.execute(prepared)

    assert result.status == "SUCCEEDED"
    assert CALLS == [{"text": "changed"}]
    assert audit.events[0]["prepared_args_hash"] == compute_args_hash({"text": "changed"})
    assert audit.events[0]["prepared_args_hash"] != compute_args_hash({"text": "original"})


async def test_invalid_original_arguments_never_reach_hook():
    from muad_agent_core.hooks import HookEvent, HookPipeline

    hooks, calls = HookPipeline(), []

    async def hook(context):
        calls.append(context)

    hooks.register(HookEvent.PRE_TOOL_USE, hook)
    pipeline = ToolExecutionPipeline(registry=_registry(), hooks=hooks)
    result = await pipeline.execute(pipeline.prepare(call_id="bad", tool_name="echo", arguments={"text": 4}))
    assert result.result.outcome.error_code == "SCHEMA_INVALID" and calls == [] and CALLS == []


def _strict_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="run",
            description="run",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "platforms": {"type": "array", "items": {"type": "string"}},
                    "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            effect=ToolEffect.EXTERNAL,
            handler=_echo_handler,
        )
    )
    return registry


async def _schema_failure(arguments: dict[str, Any]) -> str:
    pipeline = ToolExecutionPipeline(registry=_strict_registry())
    result = await pipeline.execute(
        pipeline.prepare(call_id="schema", tool_name="run", arguments=arguments)
    )
    assert result.result.outcome.error_code == "SCHEMA_INVALID"
    assert CALLS == []
    return result.result.content


async def test_schema_error_names_unexpected_field_without_echoing_values() -> None:
    content = await _schema_failure({"command": "curl secret-value", "mode": "mssp"})
    assert "unexpected field(s) mode" in content
    assert "allowed: command, platforms, timeout_sec" in content
    assert "secret-value" not in content


async def test_schema_error_names_field_with_wrong_type() -> None:
    content = await _schema_failure({"command": "ok", "platforms": "mssp"})
    assert "platforms must be array" in content
    assert "mssp" not in content


async def test_schema_error_names_missing_required_field() -> None:
    content = await _schema_failure({"platforms": ["mssp"]})
    assert "missing required field(s) command" in content


async def test_schema_error_names_numeric_bound() -> None:
    content = await _schema_failure({"command": "ok", "timeout_sec": 0})
    assert "timeout_sec must be > 0" in content
