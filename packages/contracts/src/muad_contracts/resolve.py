from __future__ import annotations

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .enums import ChannelName, SkillExecutionMode
from .model_pool import PositiveCount
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
    """调用归属引用：当前只有 RUN 一种受信执行（提炼/探针在各自任务接入时扩展 Literal）。

    单独类型而不复用容量层的 `ModelCallOwner`：这里是**公网之外的内部解析入口**，
    `id` 只作审计关联，不承担容量归属（容量由 API-17 的 owner 决定）。
    """

    type: Literal["RUN"] = "RUN"
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
