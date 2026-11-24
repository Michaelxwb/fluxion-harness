from __future__ import annotations

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .enums import ChannelName, ScheduleMode, SkillExecutionMode, SkillSource
from .model_pool import PositiveCount
from .scheduling import ToolAllowlistEntry, ToolSource
from .skill_results import frozen_skill_result_contract
from .tasks import ContractModel


class ResolveDefinitionRequest(ContractModel):
    agent_id: UUID
    actor_user_id: UUID
    #: **有真值就给真值，没有就显式省略**——不得凭空编一个通道名（B-09）。
    #: 现状：console 侧零消费方读它（`resolve_service` 只用 agent/actor），所以"必填"只会逼
    #: 每个调用点去编一个值；哪天开始按通道做授权，编造的值立刻就是错的。
    #: 定时触发（worker）与建会话（runtime）都不经渠道 ⇒ 它们显式省略。
    channel: ChannelName | None = Field(default=None, min_length=1)


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


class WorkspaceRef(ContractModel):
    """执行快照冻结的 workspace 归属：tenant + user + Agent 唯一确定一个可写目录。"""

    tenant_id: str = Field(min_length=1)
    user_id: UUID
    agent_id: UUID


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
    #: 服务端记录的唯一来源；加密/执行策略由它决定，缺失或非法一律拒绝执行（RULE-snapshot-001）。
    source: SkillSource

    @model_validator(mode="after")
    def _validate_result_contract(self) -> Self:
        frozen_skill_result_contract(self.frontmatter, checksum=self.checksum)
        return self


class ResolvedMcpServer(ContractModel):
    mcp_server_id: UUID
    key: str
    endpoint: str
    catalog_revision: int = Field(ge=0)
    catalog_hash: str | None = None
    definitions: list[dict[str, Any]] = Field(default_factory=list)
    #: 非密静态请求头（Console 写入前已校验；Runtime 原样合并，不落 Task Snapshot）。
    headers: dict[str, str] = Field(default_factory=dict)
    #: 承载 `auth_secret` 的请求头名；空表示 `Authorization: Bearer`。
    secret_header: str | None = None


class ResolveDefinitionResponse(ContractModel):
    agent: ResolvedAgent
    model: ResolvedModel
    skills: list[ResolvedSkill] = Field(default_factory=list)
    mcp_servers: list[ResolvedMcpServer] = Field(default_factory=list)


class AdmissionMaterialsRequest(ContractModel):
    """受理边界一次取齐：定义 + 平台设置快照 + 当前认证值。

    单 Console 进程时三次内部往返（定义/设置并发、再取凭据）各自付一份固定开销（DB 会话、
    路由与响应校验，实测每请求 ~3.8ms）；合并成一次请求后受理侧只付一份。
    """

    agent_id: UUID
    actor_user_id: UUID
    #: 本 Run 的 id：只作审计关联（与 API-09 的 `execution_ref` 同义），不承担容量归属。
    execution_id: UUID
    channel: ChannelName | None = Field(default=None, min_length=1)


class ScheduleAdmissionRequest(ContractModel):
    """每次 Fire 的受理取数请求（scheduled-task-v2 §2.3.4 / §3.5.5）。

    只描述「这次要执行什么」，不携带任何已解析结果：按 `mode` 取依赖范围内的材料，
    SKILL 取 `skill_id` 指向的那一个 Skill，AGENTIC 取 `tool_allowlist` 逐个工具。
    """

    agent_id: UUID
    actor_user_id: UUID
    mode: ScheduleMode
    skill_id: UUID | None = None
    tool_allowlist: list[ToolAllowlistEntry] = Field(default_factory=list)


class ScheduleModelMaterials(ContractModel):
    """冻结用的模型材料：定义 + 本次成员选择 + 池成员引用（不含任何密钥）。

    `base_url` 来自本次选中的成员凭据（非密）；`api_key` 永不进入本结构。
    REMINDER 不依赖 Model/凭据，`deployment_id`/`base_url` 显式为空。
    """

    id: UUID
    revision: int = Field(ge=1)
    model_id: str = Field(min_length=1)
    protocol: str = Field(min_length=1, max_length=16)
    params: dict[str, Any] = Field(default_factory=dict)
    base_url: str | None = None
    deployment_id: UUID | None = None
    pool_members: list[dict[str, Any]] = Field(default_factory=list)
    summary_model: dict[str, Any] | None = None


class ScheduleToolMaterials(ContractModel):
    """allowlist 中一个工具的可冻结材料：完整 `input_schema` + 归属与重试安全声明。"""

    source: ToolSource
    tool_name: str = Field(min_length=1, max_length=200)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    retry_safety: str | None = Field(default=None, max_length=16)
    server_id: UUID | None = None
    server_key: str | None = Field(default=None, max_length=128)
    catalog_revision: int | None = Field(default=None, ge=1)
    skill_id: UUID | None = None
    artifact_id: UUID | None = None
    checksum: str | None = None
    execution_mode: str | None = Field(default=None, max_length=16)


class ScheduleMemoryCandidate(ContractModel):
    """任务只读记忆的一条候选（与 `muad_agent_core.memory.selection.MemoryRecord` 同形）。"""

    memory_key: str = Field(min_length=1, max_length=128)
    memory_kind: str = Field(min_length=1, max_length=24)
    predicate: str = Field(min_length=1, max_length=64)
    scope_state: str = Field(min_length=1, max_length=16)
    scope: dict[str, str] = Field(default_factory=dict)
    content: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    cardinality: str = Field(min_length=1, max_length=8)
    priority: str = Field(min_length=1, max_length=16)
    author: str = Field(min_length=1, max_length=16)
    version: int = Field(ge=1)
    updated_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    enabled: bool = True


class ScheduleMemoryMaterials(ContractModel):
    """一次有界的记忆读取结果；读取失败时 `degraded=True` 且候选为空，不阻断受理。"""

    taxonomy_version: int = Field(ge=1)
    revision: int = Field(ge=0)
    as_of: AwareDatetime
    candidates: list[ScheduleMemoryCandidate] = Field(default_factory=list)
    degraded: bool = False
    error_code: str | None = Field(default=None, max_length=64)


class ScheduleGrantMaterials(ContractModel):
    agent_access: bool = True
    skill_ids: list[UUID] = Field(default_factory=list)
    mcp_server_ids: list[UUID] = Field(default_factory=list)


class ScheduleAdmissionMaterials(ContractModel):
    """受理材料一次取齐：快照 v4 依赖范围内的完整非密钥材料（design §3.5.7）。"""

    mode: ScheduleMode
    agent: ResolvedAgent
    model: ScheduleModelMaterials
    skills: list[ResolvedSkill] = Field(default_factory=list)
    mcp_servers: list[ResolvedMcpServer] = Field(default_factory=list)
    tools: list[ScheduleToolMaterials] = Field(default_factory=list)
    memory: ScheduleMemoryMaterials | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    prompt_template: dict[str, Any] = Field(default_factory=dict)
    grants: ScheduleGrantMaterials = Field(default_factory=ScheduleGrantMaterials)
    settings_revision: int = Field(ge=0)
    settings: dict[str, Any] = Field(default_factory=dict)


class ResolveModelRequest(ContractModel):
    """按**既有模型定义的主键**解析单个模型（ADR-06：`compaction.summary.model_ref`）。

    摘要模型不是"另一条通道上的默认模型"，它就是一条普通的模型定义：同租户、必须 enabled、
    解析失败即失败（`harness-model#RULE-model-001`：不存在平台默认模型回退）。
    """

    model_id: UUID
    actor_user_id: UUID


class ResolveModelResponse(ContractModel):
    model: ResolvedModel


class RuntimeExecutionRef(ContractModel):
    """调用归属引用：交互式 Run 或定时 Task Execution（提炼/探针在各自任务接入时扩展 Literal）。

    单独类型而不复用容量层的 `ModelCallOwner`：这里是**公网之外的内部解析入口**，
    `id` 只作审计关联，不承担容量归属（容量由容量端口的 owner 决定）。`type=TASK` 时
    `id` 是 `task.task_execution.id`，凭据解析按该 Execution 的 tenant/actor/agent 复核。
    """

    type: Literal["RUN", "TASK"] = "RUN"
    id: UUID


class ResolveCredentialsRequest(ContractModel):
    """API-09：按**冻结引用**读取所选成员的当前凭据（只进调用内存，不落盘）。

    三个冻结引用（member `deployment_id` / `credential_id` / `expected_revision`）**同生同灭**：
    给齐 = 按冻结归属解析并核对版本；缺省 = 尚未冻结成员选择的过渡解析（模型级首个可用成员，
    见 `ModelDeploymentRepository.find_execution_credential`），TASK-024/025 把 Runtime 的冻结
    引用接上后由那条链路收口。部分给齐是客户端缺陷，契约层直接拒绝。

    明文与派生指纹都不出现在响应里；响应只给"当前 Key 身份 + 版本"（`quota_group_id` 可空：
    无共享额度组的 Key 独占自身容量）。
    """

    execution_ref: RuntimeExecutionRef
    model_id: UUID
    #: 只作审计关联，不是门控：调用方身份来自服务身份 + 租户头，受信调用方可不带
    actor_user_id: UUID | None = None
    deployment_id: UUID | None = None
    credential_id: UUID | None = None
    expected_revision: PositiveCount | None = None
    mcp_server_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _frozen_refs_are_all_or_none(self) -> Self:
        frozen = (self.deployment_id, self.credential_id, self.expected_revision)
        if any(value is not None for value in frozen) and not all(
            value is not None for value in frozen
        ):
            raise ValueError("frozen member credential references must be provided together")
        return self
