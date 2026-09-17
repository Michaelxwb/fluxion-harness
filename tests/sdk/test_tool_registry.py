import pytest
from muad_agent_core.tools import (
    ToolAlreadyRegisteredError,
    ToolDefinition,
    ToolEffect,
    ToolNotFoundError,
    ToolRegistry,
)


def _tool(name: str, effect: ToolEffect = ToolEffect.READ) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        effect=effect,
    )


def test_tool_effect_members() -> None:
    assert {member.value for member in ToolEffect} == {"READ", "WRITE", "EXTERNAL"}


def test_register_get_and_list() -> None:
    registry = ToolRegistry()
    definition = _tool("load_skill")
    registry.register(definition)

    assert registry.get("load_skill") is definition
    assert registry.list() == (definition,)


def test_duplicate_tool_name_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(_tool("load_skill"))

    with pytest.raises(ToolAlreadyRegisteredError) as error:
        registry.register(_tool("load_skill"))

    assert error.value.name == "load_skill"
    assert isinstance(error.value, ValueError)


def test_unknown_tool_raises_typed_error() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError) as error:
        registry.get("missing")

    assert error.value.name == "missing"
    assert isinstance(error.value, LookupError)


def test_list_preserves_registration_order() -> None:
    registry = ToolRegistry()
    first = _tool("load_skill")
    second = _tool("execute_skill", ToolEffect.EXTERNAL)
    registry.register(first)
    registry.register(second)

    assert registry.list() == (first, second)


def test_namespaced_mcp_tool_key() -> None:
    assert ToolRegistry.namespaced("knowledge", "search") == "mcp::knowledge::search"


def test_namespaced_mcp_tool_rejects_empty_parts() -> None:
    for server, tool in (("", "search"), ("knowledge", ""), ("", "")):
        with pytest.raises(ValueError):
            ToolRegistry.namespaced(server, tool)


def test_namespaced_key_is_registrable() -> None:
    registry = ToolRegistry()
    name = ToolRegistry.namespaced("knowledge", "search")
    registry.register(_tool(name, ToolEffect.EXTERNAL))

    assert registry.get(name).name == "mcp::knowledge::search"
