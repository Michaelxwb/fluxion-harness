"""[B-113] ToolRegistry prepare/execute 统一链：schema 校验→policy→execute→审计 port。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_core.tools.pipeline import (
    PreparedToolCall,
    ToolExecutionPipeline,
    ToolPolicyDecision,
)

CALLS: list[dict[str, Any]] = []


def _echo_handler(arguments: dict[str, Any]) -> str:
    CALLS.append(dict(arguments))
    return json.dumps({"echo": arguments.get("text")})


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

    async def record(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _pipeline(audit: RecordingAudit, policy=None) -> ToolExecutionPipeline:
    return ToolExecutionPipeline(registry=_registry(), audit=audit, policy=policy)


async def test_b113_happy_path_schema_then_handler_then_audit() -> None:
    """[B-113] schema 校验→handler→审计成功终态。"""
    audit = RecordingAudit()
    pipeline = _pipeline(audit)
    prepared = pipeline.prepare(
        call_id="c1", tool_name="echo", arguments={"text": "ping"}
    )
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
    hash_plain = pipeline.prepare(
        call_id="c4", tool_name="echo", arguments={"text": "same"}
    ).args_hash
    hash_with_key = pipeline.prepare(
        call_id="c5", tool_name="echo", arguments={"text": "same", "api_key": "sk-very-secret"}
    ).args_hash
    # hash 稳定（同参数同 hash），脱敏字段不改变 hash
    assert hash_plain.startswith("sha256:")
    assert hash_plain == hash_with_key


async def test_b113_handler_failure_audits_and_reraises() -> None:
    """[B-113] handler 异常：审计失败终态后异常向上传播（不吞）。"""

    def bad_handler(arguments: dict[str, Any]) -> str:
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
