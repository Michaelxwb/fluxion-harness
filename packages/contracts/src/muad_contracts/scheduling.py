"""定时任务 v2 的共享契约（设计 §3.3 / §3.4 / §3.5.7）。

契约唯一的理由：Runtime、Worker、IM Gateway、Console 与 WebChat 消费同一份枚举与 DTO，
没有任何一侧可以自己发明状态、字段或错误语义。

三条硬约束（越界即 422/500，不做静默兜底）：

- Draft 是唯一的“待确认配置”载体，确认与生效是同一个请求；模型的创建工具只能产 Draft；
- Fire 是一次 occurrence 的台账，`revision` 是它的属性不是身份，`(schedule, scheduled_at)` 唯一；
- Snapshot v4 按模式判别：REMINDER 不带 Skill、SKILL 恰好一个、AGENTIC 冻结依赖范围内全部 Skill。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .canonical import ensure_strict_json
from .enums import (
    CancelScope,
    CompletionMode,
    ConcurrencyPolicy,
    ConfirmationSurface,
    DeliveryIntent,
    DraftKind,
    DraftStatus,
    DstEventType,
    DstFoldPolicy,
    DstGapPolicy,
    DstResolution,
    FailureClass,
    FireStatus,
    HumanInteractionStatus,
    MemoryMode,
    OperationStatus,
    RecurrenceKind,
    ScheduleEndReason,
    ScheduleMode,
    ScheduleStatus,
    SkipReason,
    TaskStatus,
    TerminalStatus,
    WaitKind,
)
from .model_pool import (
    ModelTokenUsage,
    RemoteCompletionProof,
    SettlementOutcome,
    _require_settlement_shape,
)
from .tasks import ContractModel
from .tool_runtime import TaskToolOperationOwner

SNAPSHOT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
#: Revision 的 tool_allowlist 条目来源：MCP 或 Skill 工具。
ToolSource = Literal["MCP", "SKILL"]


class Recurrence(ContractModel):
    """一次 schedule 的重复规则。P0 只有 CRON 与 ONCE，且必须显式 IANA 时区。"""

    kind: RecurrenceKind
    #: CRON 只接受 5 段标准表达式；解释在 Revision 的 timezone 本地壁钟上进行。
    cron: str | None = Field(default=None, min_length=5, max_length=128)
    run_at: AwareDatetime | None = None
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _exactly_one_trigger(self) -> Self:
        if self.kind is RecurrenceKind.CRON:
            if not self.cron or self.run_at is not None:
                raise ValueError("kind=CRON requires cron and forbids run_at")
        elif self.run_at is None or self.cron is not None:
            raise ValueError("kind=ONCE requires run_at and forbids cron")
        return self


class SchedulePolicy(ContractModel):
    """三模式共用的调度策略（Revision `schedule_policy`）。"""

    concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.SKIP_IF_RUNNING
    #: 只在 ALLOW_PARALLEL 时有意义；其它策略固定 1，不留死配置。
    max_concurrency: int = Field(default=1, ge=1, le=8)
    until: AwareDatetime | None = None
    max_occurrences: int | None = Field(default=None, ge=1)
    dst_gap_policy: DstGapPolicy = DstGapPolicy.SKIP
    dst_fold_policy: DstFoldPolicy = DstFoldPolicy.FIRST

    @model_validator(mode="after")
    def _concurrency_shape(self) -> Self:
        if self.concurrency_policy is ConcurrencyPolicy.ALLOW_PARALLEL:
            if self.max_concurrency < 2:
                raise ValueError("ALLOW_PARALLEL requires max_concurrency >= 2")
        elif self.max_concurrency != 1:
            raise ValueError("max_concurrency only applies to ALLOW_PARALLEL")
        return self


class DraftContext(ContractModel):
    """Draft 的自由形态上下文：参数与输出偏好，不含 Secret。"""

    parameters: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters", "output", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


class ReminderExecutionSpec(ContractModel):
    mode: Literal["REMINDER"] = "REMINDER"
    reminder_text: str = Field(min_length=1, max_length=2048)


class SkillExecutionSpec(ContractModel):
    mode: Literal["SKILL"] = "SKILL"
    skill_id: UUID
    input_template: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_template", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


class ToolAllowlistEntry(ContractModel):
    """AGENTIC 的允许工具：MCP 按 server_key+tool，Skill 按 skill_id+tool。"""

    source: ToolSource
    tool_name: str = Field(min_length=1, max_length=200)
    server_key: str | None = Field(default=None, min_length=1, max_length=128)
    skill_id: UUID | None = None

    @model_validator(mode="after")
    def _source_shape(self) -> Self:
        if self.source == "MCP":
            if self.server_key is None or self.skill_id is not None:
                raise ValueError("MCP entry requires server_key and forbids skill_id")
        elif self.skill_id is None or self.server_key is not None:
            raise ValueError("SKILL entry requires skill_id and forbids server_key")
        return self


class AgenticBudget(ContractModel):
    max_steps: int = Field(ge=1, le=200)
    max_tool_calls: int = Field(ge=1, le=500)
    max_tokens: int = Field(default=0, ge=0)


class AgenticExecutionSpec(ContractModel):
    mode: Literal["AGENTIC"] = "AGENTIC"
    goal: str = Field(min_length=1, max_length=4000)
    tool_allowlist: list[ToolAllowlistEntry] = Field(default_factory=list, max_length=64)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    budget: AgenticBudget
    #: 定时任务只能只读记忆：准入时冻结注入块，任务本身不得写长期记忆。
    memory_mode: MemoryMode = MemoryMode.READ_ONLY

    @field_validator("output_contract", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


ModeExecutionSpec = Annotated[
    ReminderExecutionSpec | SkillExecutionSpec | AgenticExecutionSpec, Field(discriminator="mode")
]


class ScheduleDraftRequest(ContractModel):
    """API-01：创建 Draft（CREATE 新建，UPDATE 绑定 base_config_version 做 CAS）。"""

    kind: DraftKind = DraftKind.CREATE
    mode: ScheduleMode
    #: 用户原始自然语言，原样保留。
    instruction: str = Field(min_length=1, max_length=4000)
    recurrence: Recurrence
    context: DraftContext = Field(default_factory=DraftContext)
    execution_spec: ModeExecutionSpec
    delivery_intent: DeliveryIntent = DeliveryIntent.NONE
    #: 展示名；CREATE 可空（确认时必填或由 instruction 派生），UPDATE 缺省沿用原名。
    name: str | None = Field(default=None, min_length=1, max_length=160)
    base_schedule_id: UUID | None = None
    base_config_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _mode_and_kind_shape(self) -> Self:
        if self.execution_spec.mode != self.mode.value:
            raise ValueError("execution_spec.mode must match request mode")
        update = self.kind is DraftKind.UPDATE
        if update != (self.base_schedule_id is not None):
            raise ValueError("UPDATE requires base_schedule_id; CREATE forbids it")
        if update != (self.base_config_version is not None):
            raise ValueError("UPDATE requires base_config_version; CREATE forbids it")
        return self


class SchedulePreview(ContractModel):
    """确认卡片上的事实：做什么、何时、在哪看、依赖、预算。"""

    mode: ScheduleMode
    what: str = Field(min_length=1)
    when: str = Field(min_length=1)
    first_fire_at: AwareDatetime
    delivery: DeliveryIntent
    dependencies: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)


class ScheduleDraftResponse(ContractModel):
    draft_id: UUID
    draft_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    status: DraftStatus
    preview: SchedulePreview
    expires_at: AwareDatetime


class ScheduleDraftConfirmRequest(ContractModel):
    """API-27 / API-31：确认即生效。`name` 只在 CREATE 时必填。"""

    draft_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=160)


class ScheduleDraftConfirmResponse(ContractModel):
    schedule_id: UUID
    status: ScheduleStatus
    revision_no: int = Field(ge=1)
    config_version: int = Field(ge=1)
    next_fire_at: AwareDatetime | None


class ScheduleActionRequest(ContractModel):
    """API-04/05/06（内部）与 API-19（surface）共用的动作请求。"""

    action: Literal["pause", "resume", "cancel"]
    #: 只有 cancel 有范围；`FUTURE_ONLY` 是默认，不能关闭仍被等待的 Human 交互。
    scope: CancelScope | None = None
    reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _default_scope(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("action") == "cancel" and data.get("scope") is None:
            return {**data, "scope": CancelScope.FUTURE_ONLY}
        return data

    @model_validator(mode="after")
    def _scope_only_for_cancel(self) -> Self:
        if self.action != "cancel" and self.scope is not None:
            raise ValueError("scope is only valid for action=cancel")
        return self


class LastFireSummary(ContractModel):
    scheduled_at: AwareDatetime
    status: FireStatus
    execution_status: TaskStatus | None = None
    missed_count: int = Field(default=0, ge=0)
    dst_resolution: DstResolution | None = None


class ScheduleListItem(ContractModel):
    schedule_id: UUID
    agent_id: UUID
    name: str
    mode: ScheduleMode
    status: ScheduleStatus
    timezone: str
    config_version: int = Field(ge=1)
    next_fire_at: AwareDatetime | None = None
    last_fire: LastFireSummary | None = None
    needs_attention: bool = False
    pending_human: bool = False


class ScheduleDetail(ScheduleListItem):
    revision_no: int = Field(ge=1)
    instruction: str
    normalized_goal: str
    context: DraftContext
    dependencies: list[str] = Field(default_factory=list)
    delivery_intent: DeliveryIntent
    policy: SchedulePolicy
    budget: AgenticBudget | None = None
    memory_mode: MemoryMode | None = None
    end_reason: ScheduleEndReason | None = None
    attention_reason: str | None = None


class FireItem(ContractModel):
    fire_id: UUID
    scheduled_at: AwareDatetime
    status: FireStatus
    skip_reason: SkipReason | None = None
    missed_count: int = Field(default=0, ge=0)
    dst_resolution: DstResolution | None = None
    admission_error_code: str | None = None
    execution_id: UUID | None = None
    execution_status: TaskStatus | None = None


class DstEvent(ContractModel):
    """不产生 Fire 的 DST 事实：gap 跳过与 fold 第二次跳过。"""

    event_id: UUID
    event_type: DstEventType
    local_date: str = Field(min_length=1, max_length=10)
    local_time: str = Field(min_length=1, max_length=8)
    timezone: str = Field(min_length=1, max_length=64)
    occurred_at: AwareDatetime


class FireHistoryPage(ContractModel):
    items: list[FireItem]
    dst_events: list[DstEvent] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TaskArtifactRef(ContractModel):
    """任务产物的不可变版本引用；下载必须带 version + checksum。"""

    artifact_id: UUID
    name: str
    media_type: str
    size: int = Field(ge=0)
    checksum: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    version: str = Field(min_length=1, max_length=64)


class OperationWatermark(ContractModel):
    """`wait_payload_json.operations[]` 的一项：持久水位按 operation 记，不用单一全局水位。

    `receipt_seq` 是该 operation 已接受到的结果序号，`consumed_event_id` 是最后消费的事件；
    重复/乱序结果按 `event_id` 幂等，不能因为某条早到就跳过别的 operation。
    """

    operation_id: UUID
    call_id: str = Field(min_length=1, max_length=256)
    status: OperationStatus
    receipt_seq: int = Field(default=0, ge=0)
    consumed_event_id: UUID | None = None


class AsyncWaitPayload(ContractModel):
    operations: list[OperationWatermark] = Field(min_length=1, max_length=32)


class ExecutionDetail(ContractModel):
    execution_id: UUID
    status: TaskStatus
    wait_kind: WaitKind | None = None
    wait_payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    result_artifacts: list[TaskArtifactRef] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    failure_class: FailureClass | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    snapshot_schema_version: int = Field(ge=4)
    snapshot_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _wait_shape(self) -> Self:
        waiting = self.status is TaskStatus.WAITING_EXTERNAL
        if waiting != (self.wait_kind is not None):
            raise ValueError("wait_kind is required exactly when status=WAITING_EXTERNAL")
        if not waiting and self.wait_payload is not None:
            raise ValueError("wait_payload is only valid for WAITING_EXTERNAL")
        if self.wait_kind is WaitKind.ASYNC_OPERATION:
            if self.wait_payload is None:
                raise ValueError("ASYNC_OPERATION wait requires wait_payload.operations[]")
            AsyncWaitPayload.model_validate(self.wait_payload)
        return self


class PendingHumanItem(ContractModel):
    kind: Literal["HUMAN"] = "HUMAN"
    interaction_id: UUID
    schedule_id: UUID
    execution_id: UUID
    schedule_name: str
    question: str
    deadline_at: AwareDatetime
    input_schema: dict[str, Any] | None = None
    status: HumanInteractionStatus = HumanInteractionStatus.OPEN


class PendingAttentionItem(ContractModel):
    kind: Literal["NEEDS_ATTENTION"] = "NEEDS_ATTENTION"
    schedule_id: UUID
    schedule_name: str
    reason: str
    action: str


PendingItem = Annotated[PendingHumanItem | PendingAttentionItem, Field(discriminator="kind")]


class PendingPage(ContractModel):
    items: list[PendingItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TaskCenterBadges(ContractModel):
    pending_human: int = Field(ge=0)
    needs_attention: int = Field(ge=0)


class HumanRespondRequest(ContractModel):
    """API-10 / API-18：回答 Human 提问，服务端按 input_schema 二次校验。"""

    response: dict[str, Any]

    @field_validator("response", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


class HumanRespondResponse(ContractModel):
    interaction_id: UUID
    status: HumanInteractionStatus
    execution_id: UUID
    execution_status: TaskStatus


class ConfirmScope(ContractModel):
    """Draft 的确认来源：一次用户动作只能确认一个 Draft。"""

    surface: ConfirmationSurface
    ref: str = Field(min_length=1, max_length=256)


class TaskArtifactWriteRequest(ContractModel):
    """API-28：Task-owned 产物写入/追加版本。`base_version` 是 CAS，不是提示。"""

    task_execution_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0)
    checksum: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    #: 0 表示新产物；>0 表示在既有产物上追加不可变版本。
    base_version: int = Field(ge=0)


class TaskArtifactWriteResponse(ContractModel):
    artifact_id: UUID
    version: str = Field(min_length=1, max_length=64)
    checksum: str = Field(pattern=SNAPSHOT_HASH_PATTERN)


class TaskCapacityOwner(ContractModel):
    """Task owner 的容量归属：`task_execution_id` 不复用 ToolOperation 的 `task_id`。"""

    kind: Literal["TASK"] = "TASK"
    task_execution_id: UUID


class TaskCapacityReservationRequest(ContractModel):
    """API-29：Task 的模型调用容量预占（workload 固定 BACKGROUND）。"""

    call_attempt_id: UUID
    model_id: UUID
    deployment_id: UUID
    credential_id: UUID
    credential_revision: int = Field(ge=1)
    key_resource_id: UUID
    quota_group_id: UUID | None = None
    owner: TaskCapacityOwner
    deadline_at: AwareDatetime
    reserved_tokens: int = Field(ge=1)


class TaskLeaseRenewal(ContractModel):
    """API-29 续租：只带 epoch 栅栏与调用方冻结的绝对期限（期限不在租约行）。

    owner 由受信端口按租约的 `task_execution_id` 复核，线协议不接受调用方声明的归属。
    """

    epoch: int = Field(ge=1)
    deadline_at: AwareDatetime


class TaskCallSettlement(ContractModel):
    """API-29 结算：形状不变量与主链/探针结算同一口径，只有归属身份不同。"""

    epoch: int = Field(ge=1)
    outcome: SettlementOutcome
    usage: ModelTokenUsage | None = None
    remote_completion: RemoteCompletionProof | None = None

    @model_validator(mode="after")
    def _completion_shape(self) -> Self:
        _require_settlement_shape(self.outcome, self.usage, self.remote_completion)
        return self


class TaskCapacityGrant(ContractModel):
    """API-29 预占/续租响应：只给栅栏事实，不含 Key、计量身份或密钥。"""

    reservation_id: UUID
    epoch: int = Field(ge=1)
    lease_until: AwareDatetime


class TaskCapacityRelease(ContractModel):
    """API-29 结算响应：本次是否真的释放了占用（SETTLED 幂等回放为 False）。"""

    reservation_id: UUID
    state: Literal["ACTIVE", "UNKNOWN", "SETTLED"]
    released: bool


class TaskToolOperationSubmitRequest(ContractModel):
    """API-30：Task 提交 ASYNC 工具操作。

    调用身份是 `(tenant, owner, source_tool_call_id)`；`input_hash` 不参与唯一键，
    只用于“同一 call 换了参数”的显式失败（`TOOL_OPERATION_INPUT_MISMATCH`）。
    """

    owner: TaskToolOperationOwner
    source_tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=200)
    input_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    arguments: dict[str, Any]
    completion_mode: CompletionMode = CompletionMode.JOIN

    @field_validator("arguments", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


class TaskToolOperationResult(ContractModel):
    """一次调用已就绪的结果事实；读取可重放，消费水位由 Worker 自己的事务持有。"""

    event_id: UUID
    task_event_id: UUID
    task_event_seq: int = Field(gt=0)
    terminal_status: TerminalStatus
    completed_at: AwareDatetime
    late: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _strict_result(cls, value: object) -> object:
        return ensure_strict_json(value)


class TaskToolOperationView(ContractModel):
    """API-30 的读模型：submit / 按调用身份查找 / 单条查询共用同一形状。"""

    operation_id: UUID
    owner: TaskToolOperationOwner
    source_tool_call_id: str
    #: 取消先于提交时调用内容尚不存在，此时为 None。
    tool_name: str | None = None
    completion_mode: CompletionMode
    status: OperationStatus
    cancel_requested: bool
    #: 实际提交产生的 Task（结果到达时才确定），不是 owner。
    task_id: UUID | None = None
    result: TaskToolOperationResult | None = None


class TaskToolOperationList(ContractModel):
    """统一列表封套（RULE-api-001）：`page>=1`、`1<=page_size<=MAX_PAGE_SIZE`。"""

    items: list[TaskToolOperationView]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _page_is_consistent(self) -> Self:
        if len(self.items) > self.page_size or self.total < len(self.items):
            raise ValueError("page shape is inconsistent with the returned items")
        return self


class TaskToolOperationCancelRequest(ContractModel):
    """按调用身份取消：提交回执丢失或尚未提交时也能落墓碑。

    操作者只来自受信头（`X-Actor-User-Id`），body 不声明归属。
    """

    owner: TaskToolOperationOwner
    source_tool_call_id: str = Field(min_length=1, max_length=256)


class TaskToolOperationCancelResponse(ContractModel):
    operation_id: UUID
    status: OperationStatus
    cancel_requested: bool


# ---------------------------------------------------------------------------
# Snapshot v4（§3.5.7）：按模式判别的执行快照
# ---------------------------------------------------------------------------

#: v4 是定时任务三种模式共用的快照版本；v3（单 Skill）由立即任务构建器保留到 TASK-016。
SCHEDULE_SNAPSHOT_SCHEMA_VERSION = 4

#: 快照里**绝不允许**出现的键（递归检查）：密钥只以引用形式存在，hash 也必须剥离。
FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth_secret",
        "authorization",
        "password",
        "secret",
        "token",
        "credential_json",
    }
)


def _reject_secret_keys(value: object, *, path: str = "") -> None:
    """递归拒绝密钥键；Secret 进入快照即失败，而不是“打印时再脱敏”。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_SNAPSHOT_KEYS:
                raise ValueError(f"snapshot must not embed secrets: {path}/{key}")
            _reject_secret_keys(item, path=f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, path=f"{path}[{index}]")


class SnapshotAgent(ContractModel):
    id: UUID
    key: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    instructions: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)


class SnapshotModel(ContractModel):
    id: UUID
    revision: int = Field(ge=1)
    deployment_id: UUID | None = None
    policy_hash: str | None = Field(default=None, pattern=SNAPSHOT_HASH_PATTERN)
    model_json: dict[str, Any] = Field(default_factory=dict)
    pool_members: list[dict[str, Any]] = Field(default_factory=list)
    summary_model: dict[str, Any] | None = None


class SnapshotSkill(ContractModel):
    skill_id: UUID
    artifact_id: UUID
    version: str = Field(min_length=1, max_length=64)
    checksum: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    storage_key: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=16)
    execution_mode: str = Field(min_length=1, max_length=16)
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class SnapshotTool(ContractModel):
    source: ToolSource
    tool_name: str = Field(min_length=1, max_length=200)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    schema_hash: str | None = Field(default=None, pattern=SNAPSHOT_HASH_PATTERN)
    catalog_revision: int | None = Field(default=None, ge=1)
    retry_safety: str | None = Field(default=None, max_length=16)
    skill_id: UUID | None = None
    artifact_id: UUID | None = None
    checksum: str | None = Field(default=None, pattern=SNAPSHOT_HASH_PATTERN)
    execution_mode: str | None = Field(default=None, max_length=16)


class SnapshotMcpServer(ContractModel):
    """MCP 的非密定义：`secret_header` 指出凭据 header 名，值在恢复时按凭据解析。"""

    server_id: UUID
    key: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    secret_header: str | None = Field(default=None, max_length=128)
    catalog_revision: int | None = Field(default=None, ge=1)
    catalog_hash: str | None = Field(default=None, pattern=SNAPSHOT_HASH_PATTERN)
    definitions: list[dict[str, Any]] = Field(default_factory=list)


class SnapshotMemory(ContractModel):
    """记忆注入块：存**实际注入内容**，读取失败时为空并标 degraded，不阻断任务。"""

    mode: MemoryMode = MemoryMode.READ_ONLY
    taxonomy_version: int = Field(ge=1)
    revision: int = Field(ge=0)
    payload_hash: str | None = Field(default=None, pattern=SNAPSHOT_HASH_PATTERN)
    block: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False


class SnapshotBudget(ContractModel):
    max_steps: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=0)
    deadline_at: AwareDatetime | None = None
    retry_safety: str | None = Field(default=None, max_length=16)


class SnapshotGrants(ContractModel):
    agent_access: bool = True
    skill_grants: list[UUID] = Field(default_factory=list)
    mcp_grants: list[UUID] = Field(default_factory=list)


class TaskExecutionSnapshotV4(ContractModel):
    """定时任务执行快照（`execution_snapshot_json`）。

    `schema_version` 固定 4；`skills` 的形状由 `mode` 决定：REMINDER 0 个、SKILL 恰好 1 个、
    AGENTIC 0..N 个（allowlist 涉及的 Skill artifact 全部冻结）。
    """

    schema_version: Literal[4] = 4
    mode: ScheduleMode
    snapshot_bytes: int = Field(default=0, ge=0)
    agent: SnapshotAgent
    model: SnapshotModel
    policy: dict[str, Any] = Field(default_factory=dict)
    skills: list[SnapshotSkill] = Field(default_factory=list)
    tools: list[SnapshotTool] = Field(default_factory=list)
    mcp: list[SnapshotMcpServer] = Field(default_factory=list)
    memory: SnapshotMemory | None = None
    budget: SnapshotBudget = Field(default_factory=SnapshotBudget)
    allowlist: list[str] = Field(default_factory=list)
    prompt_template: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    grants: SnapshotGrants = Field(default_factory=SnapshotGrants)

    @model_validator(mode="after")
    def _skills_by_mode(self) -> Self:
        if self.mode is ScheduleMode.REMINDER and self.skills:
            raise ValueError("REMINDER snapshot must not carry skills")
        if self.mode is ScheduleMode.SKILL and len(self.skills) != 1:
            raise ValueError("SKILL snapshot must carry exactly one skill")
        if self.mode is ScheduleMode.AGENTIC:
            skill_ids = {entry.skill_id for entry in self.skills}
            referenced = {tool.skill_id for tool in self.tools if tool.source == "SKILL"}
            if not referenced <= skill_ids:
                raise ValueError("AGENTIC snapshot must freeze every skill referenced by tools")
            if self.memory is not None and self.memory.mode is not MemoryMode.READ_ONLY:
                raise ValueError("scheduled tasks only read long-term memory")
        return self

    @model_validator(mode="after")
    def _no_secrets(self) -> Self:
        _reject_secret_keys(self.model_dump(mode="json"))
        return self


__all__ = [
    "AgenticBudget",
    "AgenticExecutionSpec",
    "AsyncWaitPayload",
    "ConfirmScope",
    "DraftContext",
    "DstEvent",
    "ExecutionDetail",
    "FireHistoryPage",
    "FireItem",
    "HumanRespondRequest",
    "HumanRespondResponse",
    "LastFireSummary",
    "ModeExecutionSpec",
    "OperationWatermark",
    "PendingAttentionItem",
    "PendingHumanItem",
    "PendingItem",
    "PendingPage",
    "Recurrence",
    "ReminderExecutionSpec",
    "ScheduleActionRequest",
    "ScheduleDetail",
    "ScheduleDraftConfirmRequest",
    "ScheduleDraftConfirmResponse",
    "ScheduleDraftRequest",
    "ScheduleDraftResponse",
    "ScheduleListItem",
    "SchedulePolicy",
    "SchedulePreview",
    "SkillExecutionSpec",
    "SNAPSHOT_HASH_PATTERN",
    "TaskArtifactRef",
    "TaskArtifactWriteRequest",
    "TaskArtifactWriteResponse",
    "TaskCapacityGrant",
    "TaskCapacityOwner",
    "TaskCapacityRelease",
    "TaskCapacityReservationRequest",
    "TaskCallSettlement",
    "TaskLeaseRenewal",
    "TaskCenterBadges",
    "TaskToolOperationOwner",
    "TaskToolOperationSubmitRequest",
    "ToolAllowlistEntry",
    "FORBIDDEN_SNAPSHOT_KEYS",
    "SCHEDULE_SNAPSHOT_SCHEMA_VERSION",
    "SnapshotAgent",
    "SnapshotBudget",
    "SnapshotGrants",
    "SnapshotMcpServer",
    "SnapshotMemory",
    "SnapshotModel",
    "SnapshotSkill",
    "SnapshotTool",
    "TaskExecutionSnapshotV4",
]
