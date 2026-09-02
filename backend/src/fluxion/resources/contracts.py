"""Resource Contract 稳定 facade 与 Registry/Snapshot 聚合模型。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluxion.resources.contract_base import (
    ResourceKind,
    ResourceStatus,
    ResourceVisibility,
    SensitiveSpecModel,
    SubjectType,
    _utc_now,
    assert_no_plaintext_secret,
)
from fluxion.resources.resource_specs import (
    EvalCaseDefinition,
    EvalSetDefinition,
    ExactResourceVersion,
    MCPDefinition,
    ModelDefinition,
    ModelPolicy,
    PluginDefinition,
    PolicyDefinition,
    ProviderDefinition,
    ResolvedModelRoute,
    RuntimeProfile,
    SecretDefinition,
    SkillDefinition,
    ToolDefinition,
    WorkflowDefinition,
)

__all__ = [
    "ARTIFACT_REF_PATTERN",
    "EffectiveCapability",
    "EvalCaseDefinition",
    "EvalSetDefinition",
    "ExactResourceVersion",
    "ExecutionSnapshot",
    "MCPDefinition",
    "MemoryEntryRef",
    "MemoryManifest",
    "ModelDefinition",
    "ModelPolicy",
    "PluginDefinition",
    "PolicyDefinition",
    "ProviderDefinition",
    "ResolvedModelRoute",
    "ResourceBinding",
    "ResourceDefinition",
    "ResourceKind",
    "ResourceStatus",
    "ResourceVisibility",
    "RuntimeProfile",
    "SecretDefinition",
    "SensitiveSpecModel",
    "SkillDefinition",
    "SubjectType",
    "ToolDefinition",
    "WorkflowDefinition",
    "assert_no_plaintext_secret",
]


class ResourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ResourceKind
    id: str
    tenant_id: str
    version: str
    status: ResourceStatus = ResourceStatus.DRAFT
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE
    spec_json: dict[str, object]
    created_at: datetime = Field(default_factory=_utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.id.strip():
            raise ValueError("id is required")
        if not self.version.strip():
            raise ValueError("version is required")
        if len(self.tenant_id) > 128:
            raise ValueError("tenant_id exceeds 128 characters")
        if len(self.id) > 255:
            raise ValueError("id exceeds 255 characters")
        if len(self.version) > 64:
            raise ValueError("version exceeds 64 characters")
        assert_no_plaintext_secret(self.spec_json, "spec_json")
        return self


class ResourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str
    tenant_id: str
    subject_type: SubjectType | str
    subject_id: str
    resource_type: ResourceKind
    resource_id: str
    resource_version_selector: str = "latest-published"
    config_json: dict[str, object] | None = None
    credential_ref: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.binding_id.strip():
            raise ValueError("binding_id is required")
        if not self.subject_id.strip():
            raise ValueError("subject_id is required")
        if len(self.tenant_id) > 128:
            raise ValueError("tenant_id exceeds 128 characters")
        if len(self.binding_id) > 128:
            raise ValueError("binding_id exceeds 128 characters")
        if len(self.subject_id) > 128:
            raise ValueError("subject_id exceeds 128 characters")
        if self.config_json is not None:
            assert_no_plaintext_secret(self.config_json, "config_json")
        if self.credential_ref is not None and not self.credential_ref.startswith("secret://"):
            raise ValueError("credential_ref must use secret:// SecretRef")
        return self


class MemoryEntryRef(BaseModel):
    """进入 Execution 的 Personal Memory 条目引用（closure TASK-001）。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    memory_type: str
    content_hash: str
    priority: int = 0


class MemoryManifest(BaseModel):
    """Memory 检索清单：entry refs + 联合 content hash + 截断标记。"""

    model_config = ConfigDict(extra="forbid")

    entry_refs: list[MemoryEntryRef] = Field(default_factory=list)
    content_hash: str = ""
    truncated: bool = False


# Phase 5 TASK-001（design §3.3）：artifact 引用语法在契约层定义（规范形态），
# plugins/artifact 的 ref 模型复用同一 grammar——Kernel 不依赖 Plugin 方向保持。
ARTIFACT_REF_PATTERN = re.compile(r"^artifact://([^/@\s]+)/([^/@\s]+)/([^@\s]+)@([^/@\s]+)$")


class EffectiveCapability(BaseModel):
    """TASK-007：EffectiveCapability 图——Tool/MCP/Skill/Workflow 授权/依赖/运行
    要求统一领域模型（替代 ad-hoc dict），构建期冻结进 Snapshot、执行期只读。"""

    model_config = ConfigDict(frozen=True)

    skills: dict[str, str] = Field(default_factory=dict)  # ref -> exact version
    mcps: dict[str, str] = Field(default_factory=dict)  # ref -> exact version
    tools: list[str] = Field(default_factory=list)  # tool capability refs
    workflows: list[str] = Field(default_factory=list)  # workflow refs


class ExecutionSnapshot(BaseModel):
    # frozen=True 落实 ADR-005 的执行期不可变：持有者不能原地改写
    # model_resolution 等字段。构造时另对派生自 profile spec_json 的
    # model_resolution 深拷贝，断开与 Registry 缓存的共享引用。
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    runtime_profile_version: str
    # TASK-A104：persona/model/capability 产品语义迁至 AgentDefinition（PRD §4.3
    # Snapshot 冻结 AgentDefinition exact version）；无关联 Agent 时为 None。
    agent_definition_id: str | None = None
    agent_definition_version: str | None = None
    # FEAT-02：AgentDefinition 的三条运行依赖引用进 Snapshot（exact version 冻结，
    # 缺省 None 为 fail-safe——解析失败 fail-closed 由 resolver 保证）。
    workflow_ref: ExactResourceVersion | None = None
    memory_policy_ref: ExactResourceVersion | None = None
    personalization_policy_ref: ExactResourceVersion | None = None
    # ADR-012：结构化 ModelPolicy（frozen）。validate 产生的新实例天然与缓存
    # spec_json 断开引用，执行期不可变由 model 层保证（原为 deepcopy dict 防护）。
    model_resolution: ModelPolicy
    trace_id: str
    system_prompt: str = ""
    skill_instructions: dict[str, str] = Field(default_factory=dict)
    skill_required_capabilities: list[str] = Field(default_factory=list)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    mcp_versions: dict[str, str] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)
    policy_version: str | None = None
    binding_versions: dict[str, str] = Field(default_factory=dict)
    # closure TASK-001（phase2）V2 字段：版本图谱全集（remediation §13.2）。
    user_profile_version: str | None = None
    policy_versions: dict[str, str] | None = None
    credential_versions: dict[str, str] | None = None
    # FEAT-01/02：effective 能力/权限图（授权结果，进 canonical digest，执行期只读）。
    effective_capability: EffectiveCapability = Field(default_factory=EffectiveCapability)
    effective_permissions: dict[str, object] = Field(default_factory=dict)
    # Phase 5 TASK-001：本次执行 pin 的 artifact 引用（name →
    # artifact://{tenant}/{ns}/{key}@{version}，规则 6/10——published 不可变、
    # 回滚选历史版本）。引用来自 Resource spec（ExecutionSnapshot 固定版本）。
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    memory_manifest: MemoryManifest | None = None
    snapshot_digest: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if not self.tenant_id.strip() or not self.user_id.strip():
            raise ValueError("tenant_id and user_id are required")
        if not self.trace_id.strip():
            raise ValueError("trace_id is required")
        for ref_name, ref in self.artifact_refs.items():
            if ARTIFACT_REF_PATTERN.match(ref) is None:
                raise ValueError(
                    f"artifact_ref {ref_name!r} must use "
                    "artifact://{tenant}/{namespace}/{key}@{version}"
                )
        return self
