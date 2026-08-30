"""AgentDefinition typed spec model（TASK-A101，PRD §4.2）。

AgentDefinition 是产品/逻辑实体：persona 与模型、能力、运行态全部经引用表达
（model_ref / runtime_profile_ref / capabilities），自身 spec_json 不内嵌
RuntimeProfile 的产品语义字段（TASK-A104 收缩后的边界）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from fluxion.resources.contracts import ExactResourceVersion, SensitiveSpecModel


class CapabilityType(StrEnum):
    """capability 来源类型（CLAUDE.md 规则 12：Tool 是 Agent-facing Adapter，
    skill/tool/mcp/workflow 统一经 Capability Contract 绑定，不设独立 tools 字段）。"""

    SKILL = "skill"
    TOOL = "tool"
    MCP = "mcp"
    WORKFLOW = "workflow"


class AgentCapabilityReference(SensitiveSpecModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_ref: str = Field(
        min_length=1,
        max_length=255,
        title="能力引用",
        description="capability 资源 ID（skill/mcp/plugin 资源）",
    )
    version_pin: str = Field(
        min_length=1,
        max_length=64,
        title="固定版本",
        description="绑定的精确版本（进 ExecutionSnapshot 后不漂移）",
    )
    type: CapabilityType = Field(title="能力类型", description="能力类型：skill/tool/mcp")


class AgentDefinition(SensitiveSpecModel):
    """Agent 产品领域实体（版本化 Resource 的 spec 形态）。

    分组对齐 design/08 与 closure 契约：identity/presentation、owner、
    runtime_profile_ref、default capability/workflow presentation、
    memory/personalization policy refs；model_ref 由 ExecutionSnapshot 冻结。

    状态唯一事实源（P1C-01 收口）：status/visibility 只由外层
    ResourceDefinition envelope 承载；spec 内的 legacy ``lifecycle``/
    ``visibility`` 键读取时剥离（存量兼容，不批量重写），序列化不再产出。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, title="Agent 名", description="Agent 展示名")
    description: str = Field(
        default="", max_length=512, title="用途说明", description="用途说明"
    )
    system_prompt: str = Field(
        min_length=1, max_length=8192, title="系统提示词", description="人设/指令前缀"
    )
    owner: str = Field(
        min_length=1, max_length=128, title="归属", description="归属（tenant 内用户/团队）"
    )
    model_ref: ExactResourceVersion = Field(
        title="模型引用", description="模型 provider 资源的精确版本引用（id + version）"
    )
    runtime_profile_ref: ExactResourceVersion | None = Field(
        default=None,
        title="运行态引用",
        description="RuntimeProfile 引用；留空由解析层取租户默认",
    )
    capabilities: list[AgentCapabilityReference] = Field(
        default_factory=list,
        title="能力绑定",
        description="capability 绑定列表（skill/tool/mcp typed；不设独立 tools 字段）",
    )
    workflow_ref: ExactResourceVersion | None = Field(
        default=None, title="默认工作流", description="default workflow 引用"
    )
    memory_policy_ref: ExactResourceVersion | None = Field(
        default=None,
        title="记忆策略引用",
        description="MemoryPolicy 引用（Phase 2 深做，契约先锁定）",
    )
    personalization_policy_ref: ExactResourceVersion | None = Field(
        default=None,
        title="个性化策略引用",
        description="PersonalizationPolicy 引用（Phase 2 深做，契约先锁定）",
    )
    instructions: str = Field(
        default="", max_length=2048, title="补充指令", description="补充指令"
    )
