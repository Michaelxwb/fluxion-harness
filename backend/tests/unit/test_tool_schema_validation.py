from __future__ import annotations

import pytest

from fluxion.runtime.tools import (
    ToolDescriptor,
    ToolRuntime,
    ToolRuntimeError,
    _validate_arguments,
)
from tests.runtime_helpers import minimal_tool_context

# S-02 / E-01：Tool 参数完整 JSON Schema 校验（type/enum/required/nested/additionalProperties）。
SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "mode": {"type": "string", "enum": ["fast", "deep"]},
        "filters": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        tool_id="search",
        capability_id="cap.search",
        name="search",
        parameters_schema=SCHEMA,
    )


def test_S02_valid_arguments_pass_full_schema_validation() -> None:
    # 合法：type/enum/required/nested 全覆盖，不抛异常。
    _validate_arguments(
        _descriptor(),
        {"query": "weather", "mode": "deep", "filters": {"limit": 10}},
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x", "mode": "turbo"},  # enum 非法
        {"query": 42},  # type 非法
        {},  # required 缺失
        {"query": "x", "filters": {"limit": "many"}},  # nested type 非法
        {"query": "x", "filters": {"limit": 1, "sort": "desc"}},  # nested additionalProperties
        {"query": "x", "unknown": "y"},  # 顶层 additionalProperties
    ],
)
def test_S02_invalid_arguments_are_rejected(arguments: dict[str, object]) -> None:
    with pytest.raises(ToolRuntimeError):
        _validate_arguments(_descriptor(), arguments)


@pytest.mark.asyncio
async def test_E01_call_path_returns_clear_validation_error() -> None:
    # E-01：参数 type/enum 不符 → ToolRuntime.call 拒绝并返回明确校验错误。
    context = minimal_tool_context(
        {
            "user_tools": ["search"],
            "agent_tools": ["search"],
            "tenant_tools": ["search"],
        }
    )
    runtime = ToolRuntime()
    runtime.register(_descriptor(), lambda ctx, args: {"ok": True})

    with pytest.raises(ToolRuntimeError) as exc_info:
        await runtime.call(context, "search", {"query": "x", "mode": "turbo"})

    assert "search" in str(exc_info.value)
    assert "invalid arguments" in str(exc_info.value)
