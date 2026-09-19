"""[E-02] SkillContext 公开面契约：无 Secret 访问接口，能力清单与 docs/06 §5 一致。"""

from __future__ import annotations

import dataclasses
from typing import get_type_hints

import pytest
from muad_skill_sdk import SkillContext


def test_e02_context_has_no_secret_surface() -> None:
    """[E-02] Skill 试图直接访问 Secret：SDK 无此接口，Secret 不进入 Skill context。"""
    field_names = {field.name for field in dataclasses.fields(SkillContext)}
    assert field_names == {"user", "logger", "artifact", "platform", "mcp", "task", "http"}
    for field in dataclasses.fields(SkillContext):
        assert "secret" not in field.name.lower()
        assert "secret" not in str(field.type).lower()

    type_hints = get_type_hints(SkillContext)
    for port in type_hints.values():
        if not hasattr(port, "__mro__"):
            continue
        for attribute in dir(port):
            if attribute.startswith("_"):
                continue
            assert "secret" not in attribute.lower(), f"{port.__name__}.{attribute} 暴露 Secret 面"


@pytest.mark.parametrize(
    "port_name",
    ["artifact", "platform", "mcp", "task", "http"],
)
def test_context_public_methods_match_contract(port_name: str) -> None:
    """契约测试：各端口公开方法集合与 docs/06 §5 清单一致，无额外方法。"""
    expected = {
        "artifact": {"read", "write"},
        "platform": {"call", "request"},
        "mcp": {"call"},
        "task": {"map"},
        "http": {"get", "post", "request"},
    }
    port_type = get_type_hints(SkillContext)[port_name]
    public = {
        name
        for name in dir(port_type)
        if not name.startswith("_") and callable(getattr(port_type, name, None))
    }
    assert public == expected[port_name], f"{port_name} 公开面漂移: {public ^ expected[port_name]}"
