"""ToolRegistry 的冻结装配：**只构造 allowlist 内、且冻结授权仍成立**的工具。

三层，顺序固定，缺一层都不算"授权过"：

1. **allowlist**（Snapshot `allowlist`）：快照里没有的工具名根本不注册——模型看不到它；
2. **有效能力**（Snapshot `grants`）：`agent_access` 为假、Skill 不在 `skill_grants`、
   MCP server 不在 `mcp_grants` 的工具即使出现在 allowlist 也不注册；
3. **每次调用的冻结 Tool Policy**：注册表之外的名字被管线拒绝（`TOOL_NOT_ALLOWED`），
   未授权工具不产生任何外部请求。

工具 schema 只取自快照（`tools[].input_schema` / MCP `definitions`）：不在执行期查 catalog，
也不因为当前配置变化而扩展。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from muad_agent_core.tools import (
    PreparedToolCall,
    ToolDefinition,
    ToolEffect,
    ToolPolicyDecision,
    ToolRegistry,
)
from muad_contracts import SnapshotSkill, SnapshotTool, ToolRetrySafety

from .mcp import McpToolBox, frozen_mcp_tool_schema, mcp_tool_name, mcp_tool_trust
from .snapshot import FrozenAgenticExecution

TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
UNKNOWN_TOOL_SCHEMA = {"type": "object", "additionalProperties": False}


class SkillToolRunner(Protocol):
    """SYNC/ASYNC Skill 工具的执行者（异步提交与挂起由它自己完成）。"""

    async def call(
        self, skill: SnapshotSkill, tool_name: str, arguments: Mapping[str, Any], *, call_id: str
    ) -> str: ...


def allowlist_key(
    source: str, *, tool_name: str, server_key: str | None = None, skill_id: UUID | None = None
) -> str:
    if source == "MCP":
        return f"MCP:{server_key}:{tool_name}"
    return f"SKILL:{skill_id}:{tool_name}"


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    allowlist_key: str
    source: str
    skill_id: UUID | None = None
    server_id: UUID | None = None


class AgenticToolRegistry:
    """装配结果：注册表 + 允许清单 + 每次调用的策略。"""

    def __init__(self, registry: ToolRegistry, allowed: Mapping[str, RegisteredTool]) -> None:
        self.registry = registry
        self.allowed = dict(allowed)

    def policy(self, call: PreparedToolCall) -> ToolPolicyDecision:
        entry = self.allowed.get(call.tool_name)
        if entry is None:
            return ToolPolicyDecision(False, "tool is not in the frozen allowlist", TOOL_NOT_ALLOWED)
        return ToolPolicyDecision(True)


def build_registry(
    frozen: FrozenAgenticExecution,
    *,
    mcp: McpToolBox | None,
    skills: SkillToolRunner | None,
) -> AgenticToolRegistry:
    """按快照构造注册表；未注册 = 模型看不到、调用即 `unknown tool`。"""
    snapshot = frozen.snapshot
    allowlist = set(snapshot.allowlist)
    grants = snapshot.grants
    registry = ToolRegistry()
    allowed: dict[str, RegisteredTool] = {}
    if not grants.agent_access:
        return AgenticToolRegistry(registry, allowed)
    skill_ids = set(grants.skill_grants)
    mcp_ids = set(grants.mcp_grants)
    _register_mcp(frozen, registry, allowed, allowlist, mcp_ids, mcp)
    _register_skills(frozen, registry, allowed, allowlist, skill_ids, skills)
    return AgenticToolRegistry(registry, allowed)


def _register_mcp(
    frozen: FrozenAgenticExecution,
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    allowlist: set[str],
    mcp_ids: set[UUID],
    mcp: McpToolBox | None,
) -> None:
    if mcp is None:
        return
    for server in frozen.snapshot.mcp:
        if server.server_id not in mcp_ids:
            continue
        for definition in server.definitions:
            tool_name = definition.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            key = allowlist_key("MCP", tool_name=tool_name, server_key=server.key)
            if key not in allowlist:
                continue
            schema = frozen_mcp_tool_schema(server, tool_name)
            if schema is None:
                continue
            trust = mcp_tool_trust(tuple(frozen.snapshot.tools), tool_name)
            name = mcp_tool_name(server.key, tool_name)
            registry.register(
                ToolDefinition(
                    name=name,
                    description=str(definition.get("description") or tool_name),
                    input_schema=schema,
                    effect=ToolEffect.READ,
                    retry_safety=_retry_safety(trust),
                    handler=_mcp_handler(mcp, server.server_id, tool_name),
                )
            )
            allowed[name] = RegisteredTool(name, key, "MCP", server_id=server.server_id)


def _mcp_handler(mcp: McpToolBox, server_id: UUID, tool_name: str) -> Any:
    async def call(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return await mcp.call(server_id, tool_name, arguments)

    return call


def _register_skills(
    frozen: FrozenAgenticExecution,
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    allowlist: set[str],
    skill_ids: set[UUID],
    skills: SkillToolRunner | None,
) -> None:
    if skills is None:
        return
    skills_by_id = {entry.skill_id: entry for entry in frozen.snapshot.skills}
    for tool in frozen.snapshot.tools:
        if tool.source != "SKILL" or tool.skill_id is None:
            continue
        skill = skills_by_id.get(tool.skill_id)
        if skill is None or tool.skill_id not in skill_ids:
            continue
        key = allowlist_key("SKILL", tool_name=tool.tool_name, skill_id=tool.skill_id)
        if key not in allowlist:
            continue
        name = skill_tool_name(tool.skill_id, tool.tool_name)
        registry.register(
            ToolDefinition(
                name=name,
                description=f"skill {skill.skill_id} tool {tool.tool_name}",
                input_schema=dict(tool.input_schema) or UNKNOWN_TOOL_SCHEMA,
                effect=ToolEffect.EXTERNAL if skill.execution_mode == "ASYNC" else ToolEffect.WRITE,
                retry_safety=_retry_safety(tool),
                handler=_skill_handler(skills, skill, tool.tool_name),
            )
        )
        allowed[name] = RegisteredTool(name, key, "SKILL", skill_id=tool.skill_id)


def _skill_handler(runner: SkillToolRunner, skill: SnapshotSkill, tool_name: str) -> Any:
    async def call(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return await runner.call(skill, tool_name, arguments, call_id=call_id)

    return call


def skill_tool_name(skill_id: UUID, tool_name: str) -> str:
    return f"skill::{skill_id}::{tool_name}"


def _retry_safety(tool: SnapshotTool | None) -> ToolRetrySafety:
    if tool is None or not tool.retry_safety:
        return ToolRetrySafety.NON_RETRYABLE
    try:
        return ToolRetrySafety(tool.retry_safety)
    except ValueError:
        return ToolRetrySafety.NON_RETRYABLE


def policy_for(agentic: AgenticToolRegistry) -> Callable[[PreparedToolCall], ToolPolicyDecision]:
    return agentic.policy


__all__ = [
    "AgenticToolRegistry",
    "RegisteredTool",
    "SkillToolRunner",
    "TOOL_NOT_ALLOWED",
    "allowlist_key",
    "build_registry",
    "policy_for",
    "skill_tool_name",
]
