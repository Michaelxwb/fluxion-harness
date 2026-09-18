from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .enums import SkillExecutionMode
from .tasks import ContractModel


class ResolveDefinitionRequest(ContractModel):
    agent_id: UUID
    actor_user_id: UUID
    channel: Literal["WECOM"]


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
    tools: list[dict[str, Any]] = Field(default_factory=list)


class ResolveDefinitionResponse(ContractModel):
    agent: ResolvedAgent
    model: ResolvedModel
    skills: list[ResolvedSkill] = Field(default_factory=list)
    mcp_servers: list[ResolvedMcpServer] = Field(default_factory=list)
