from __future__ import annotations

from collections.abc import Mapping

from fluxion.runtime.context import RuntimeContext


def frozen_tool_policy(
    context: RuntimeContext,
    mcp_tool_ids: set[str] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """从 frozen effective_permissions 读取 Tool 授权三元组。"""
    permissions = context.snapshot.effective_permissions or {}
    user = _permission_set(permissions, "user_tools")
    agent = _permission_set(permissions, "agent_tools")
    tenant = _permission_set(permissions, "tenant_tools")
    denied = _permission_set(permissions, "denied_tools")
    mode = str(permissions.get("tenant_tool_policy", "allow_list"))
    mcp = set(mcp_tool_ids or ()) - denied
    user |= mcp
    agent |= mcp
    if mode == "deny_only":
        tenant = (user | agent) - denied
    return user, agent, tenant


def _permission_set(permissions: Mapping[str, object], key: str) -> set[str]:
    value = permissions.get(key, [])
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}
