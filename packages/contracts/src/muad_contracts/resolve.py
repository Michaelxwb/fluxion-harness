from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .enums import ChannelName, SkillExecutionMode
from .tasks import ContractModel


class ResolveDefinitionRequest(ContractModel):
    agent_id: UUID
    actor_user_id: UUID
    #: **有真值就给真值，没有就显式省略**——不得凭空编一个通道名（B-09）。
    #: 现状：console 侧零消费方读它（`resolve_service` 只用 agent/actor），所以"必填"只会逼
    #: 每个调用点去编一个值；哪天开始按通道做授权，编造的值立刻就是错的。
    #: 定时触发（worker）与建会话（runtime）都不经渠道 ⇒ 它们显式省略。
    channel: ChannelName | None = None


class ResolvedAgent(ContractModel):
    id: UUID
    key: str
    revision: int = Field(ge=1)
    instructions: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)


class ResolvedModel(ContractModel):
    id: UUID
    revision: int = Field(ge=1)
    protocol: Literal["OPENAI"] = "OPENAI"
    model_id: str
    base_url: str
    api_key: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ResolvedSkill(ContractModel):
    skill_id: UUID
    artifact_id: UUID
    key: str
    name: str
    description: str
    version: str
    checksum: str
    storage_key: str
    execution_mode: SkillExecutionMode = SkillExecutionMode.SYNC
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class ResolvedMcpServer(ContractModel):
    mcp_server_id: UUID
    key: str
    endpoint: str
    catalog_revision: int = Field(ge=0)
    catalog_hash: str | None = None
    definitions: list[dict[str, Any]] = Field(default_factory=list)


class ResolveDefinitionResponse(ContractModel):
    agent: ResolvedAgent
    model: ResolvedModel
    skills: list[ResolvedSkill] = Field(default_factory=list)
    mcp_servers: list[ResolvedMcpServer] = Field(default_factory=list)


class ResolveModelRequest(ContractModel):
    """按**既有模型定义的主键**解析单个模型（ADR-06：`compaction.summary.model_ref`）。

    摘要模型不是"另一条通道上的默认模型"，它就是一条普通的模型定义：同租户、必须 enabled、
    解析失败即失败（`harness-model#RULE-model-001`：不存在平台默认模型回退）。
    """

    model_id: UUID
    actor_user_id: UUID


class ResolveModelResponse(ContractModel):
    model: ResolvedModel
