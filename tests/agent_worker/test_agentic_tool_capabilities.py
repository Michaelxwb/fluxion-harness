"""Capability assembly: prompt promises must correspond to registered tools."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from muad_agent_core.tools import (
    ToolAssembly,
    ToolCall,
    ToolCapability,
    ToolCapabilityMissing,
    ToolDefinition,
    ToolEffect,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
)


async def _handler(arguments: Mapping[str, object], *, call: ToolCall) -> ToolResult:
    return ToolResult("ok")


def _definition(name: str, *capabilities: ToolCapability) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        handler=_handler,
        capabilities=frozenset(capabilities),
    )


def test_require_rejects_a_prompt_capability_without_a_tool() -> None:
    registry = ToolRegistry()
    registry.register(_definition("load_skill", ToolCapability.SKILL_INSTRUCTIONS))
    assembly = ToolAssembly(registry)

    assembly.require(ToolCapability.SKILL_INSTRUCTIONS)

    with pytest.raises(ToolCapabilityMissing) as missing:
        assembly.require(ToolCapability.SKILL_EXECUTE)
    assert missing.value.capability is ToolCapability.SKILL_EXECUTE


def test_registry_has_no_compatibility_namespace() -> None:
    registry = ToolRegistry()
    assert not hasattr(registry, "register_compatibility")
    with pytest.raises(ToolNotFoundError):
        registry.get("run_skill_script")
