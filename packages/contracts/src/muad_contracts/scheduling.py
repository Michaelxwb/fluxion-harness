"""定时任务 v2 的共享契约（设计 §3.3 / §3.4 / §3.5.7）。

契约唯一的理由：Runtime、Worker、IM Gateway、Console 与 WebChat 消费同一份枚举与 DTO，
没有任何一侧可以自己发明状态、字段或错误语义。

三条硬约束（越界即 422/500，不做静默兜底）：

- Draft 是唯一的“待确认配置”载体，确认与生效是同一个请求；模型的创建工具只能产 Draft；
- Fire 是一次 occurrence 的台账，`revision` 是它的属性不是身份，`(schedule, scheduled_at)` 唯一；
- Snapshot v4 按模式判别：REMINDER 不带 Skill、SKILL 恰好一个、AGENTIC 冻结依赖范围内全部 Skill。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    field_validator,
    model_validator,
)

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
    HumanRouteOutcome,
    MemoryMode,
    OperationStatus,
    PayloadKind,
    RecurrenceKind,
    ScheduleEndReason,
    ScheduleMode,
    ScheduleStatus,
    SkipReason,
    TaskStatus,
    TaskType,
    TerminalStatus,
    WaitKind,
)
from .model_pool import (
    ModelTokenUsage,
    RemoteCompletionProof,
    SettlementOutcome,
    _require_settlement_shape,
)
from .skill_invocation import validate_skill_argv
from .tasks import ContractModel, DeliveryRouteInput
from .tool_runtime import TaskToolOperationOwner

SNAPSHOT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
#: 聊天入口的用户确认动作：用户显式回复 `/confirm <draft_id>`，由 Gateway 核验后代理 API-31。
#: 放在契约里是因为 Runtime 要把它写进工具回执（让模型原样告诉用户），Gateway 要解析它。
SCHEDULE_CONFIRM_COMMAND = "/confirm"
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


class DraftSource(ContractModel):
    """Draft 的来源入口（design §2.3.6）：**只作审计**，不是数据隔离维度。

    入口不是可见性边界（同一 user+agent 的 Schedule 跨入口共享）；这里记下"在哪创建"，
    供追溯与问题推送目标核对。`origin_bot_account_id` 是入口账号的逻辑 UUID。
    """

    origin_bot_account_id: UUID | None = None
    origin_channel: str | None = Field(default=None, min_length=1, max_length=32)


class DraftContext(ContractModel):
    """Draft 的自由形态上下文：参数与输出偏好，不含 Secret。"""

    parameters: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    source: DraftSource = Field(default_factory=DraftSource)

    @field_validator("parameters", "output", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)


class ToolAllowlistEntry(ContractModel):
    """AGENT_TURN 的允许工具：MCP 按 server_key+tool，Skill 按 skill_id+tool。"""

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


def _validate_tool_entry(entry: str) -> None:
    """工具策略条目只接受平台词表；未知前缀是理解错误，不静默丢弃。"""
    if entry == "*":
        return
    parts = entry.split(":", 2)
    if parts[0] == "PLATFORM" and len(parts) == 2 and parts[1]:
        return
    if parts[0] == "MCP" and len(parts) == 3 and parts[1] and parts[2]:
        return
    if parts[0] == "SKILL" and len(parts) == 3 and parts[2]:
        try:
            UUID(parts[1])
        except ValueError as exc:
            raise ValueError("SKILL tools_allow entries must carry a skill UUID") from exc
        return
    raise ValueError(
        "tools_allow entries must be '*', PLATFORM:<tool>, MCP:<server>:<tool> "
        "or SKILL:<uuid>:<tool>"
    )


class ScheduleWindow(ContractModel):
    """SKILL_PLAN 的数据窗口；具体渲染在 fire 时冻结（design §10.3）。"""

    kind: Literal["DAY"] = "DAY"
    offset: int = Field(default=0, ge=-31, le=31)


class ReminderPayload(ContractModel):
    """纯通知：无模型、无工具，结果就是固定文本。"""

    kind: Literal["REMINDER"] = "REMINDER"
    text: str = Field(min_length=1, max_length=2048)


class SkillPlanPayload(ContractModel):
    """固定 Skill 调用：`input` 只走 stdin，`argv` 追加到脚本（design §10.3 / §10.6）。"""

    kind: Literal["SKILL_PLAN"] = "SKILL_PLAN"
    skill_id: UUID
    input: dict[str, Any] = Field(default_factory=dict)
    argv: list[str] = Field(default_factory=list)
    window: ScheduleWindow = Field(default_factory=ScheduleWindow)

    @field_validator("input", mode="before")
    @classmethod
    def _strict_json(cls, value: object) -> object:
        return ensure_strict_json(value)

    @field_validator("argv", mode="before")
    @classmethod
    def _argv_shape(cls, value: object) -> tuple[str, ...]:
        return validate_skill_argv(value)

    @field_validator("argv")
    @classmethod
    def _argv_list(cls, value: list[str]) -> list[str]:
        # 保留 Pydantic 的 list 公开形状（tuple 只用于边界校验）。
        return list(value)


class AgentTurnPayload(ContractModel):
    """完整 prompt 模式：prompt + 可选模型 + 工具面 + 预算引用（design §10.3 / §10.7）."""

    kind: Literal["AGENT_TURN"] = "AGENT_TURN"
    prompt: str = Field(min_length=1, max_length=8000)
    model_ref: str | None = Field(default=None, min_length=1, max_length=128)
    #: 空列表 = 跟随 owner（`ToolPolicy` 解析为 `*`）；平台只读工具用 `PLATFORM:<tool>`。
    tools_allow: list[str] = Field(default_factory=list, max_length=64)
    #: 预算唯一来源是 `PlatformSettings.agent`；引用是稳定键而不是数值。
    budget_ref: str = Field(default="platform", min_length=1, max_length=64)

    @field_validator("tools_allow")
    @classmethod
    def _tools_shape(cls, value: list[str]) -> list[str]:
        entries = [entry.strip() for entry in value]
        if any(not entry for entry in entries):
            raise ValueError("tools_allow entries must be non-empty")
        if len(set(entries)) != len(entries):
            raise ValueError("tools_allow entries must be unique")
        if "*" in entries and len(entries) > 1:
            raise ValueError("tools_allow '*' cannot be combined with explicit tools")
        for entry in entries:
            _validate_tool_entry(entry)
        return entries

    def allowlist_entries(self) -> list[ToolAllowlistEntry]:
        """`SKILL:`/`MCP:` 字符串 → 结构化条目；平台工具不是材料依赖，由运行时注册。"""
        entries: list[ToolAllowlistEntry] = []
        for raw in self.tools_allow:
            parts = raw.split(":", 2)
            if parts[0] == "SKILL" and len(parts) == 3:
                entries.append(
                    ToolAllowlistEntry(source="SKILL", skill_id=UUID(parts[1]), tool_name=parts[2])
                )
            elif parts[0] == "MCP" and len(parts) == 3:
                entries.append(
                    ToolAllowlistEntry(source="MCP", server_key=parts[1], tool_name=parts[2])
                )
        return entries


SchedulePayload = Annotated[
    ReminderPayload | SkillPlanPayload | AgentTurnPayload, Field(discriminator="kind")
]

_PAYLOAD_ADAPTER: TypeAdapter[SchedulePayload] = TypeAdapter(SchedulePayload)

#: PayloadKind → 内部派生的 ScheduleMode；调度器/Snapshot 只读这一份映射。
PAYLOAD_MODE: dict[PayloadKind, ScheduleMode] = {
    PayloadKind.REMINDER: ScheduleMode.REMINDER,
    PayloadKind.SKILL_PLAN: ScheduleMode.SKILL,
    PayloadKind.AGENT_TURN: ScheduleMode.AGENTIC,
}


def parse_schedule_payload(raw: object) -> SchedulePayload:
    """载荷边界：未知 kind/形状显式失败，不做猜测或降级。"""
    return _PAYLOAD_ADAPTER.validate_python(raw)


def derive_schedule_mode(payload: SchedulePayload) -> ScheduleMode:
    """`mode` 是内部派生字段，不是用户可写选项。"""
    return PAYLOAD_MODE[PayloadKind(payload.kind)]


class SessionTarget(RootModel[str]):
    """`isolated | current | session:<id> | main` 的类型化字符串（design §10.8）。"""

    model_config = ConfigDict(frozen=True)

    root: str

    @field_validator("root")
    @classmethod
    def _shape(cls, value: str) -> str:
        if value in ("isolated", "current", "main"):
            return value
        prefix, _, session_id = value.partition(":")
        if prefix != "session" or not session_id:
            raise ValueError("session_target must be isolated, current, main or session:<id>")
        try:
            UUID(session_id)
        except ValueError as exc:
            raise ValueError("session_target session id must be a UUID") from exc
        return value

    @property
    def kind(self) -> Literal["isolated", "current", "session", "main"]:
        if self.root.startswith("session:"):
            return "session"
        return cast(
            Literal["isolated", "current", "session", "main"],
            self.root,
        )

    @property
    def session_id(self) -> UUID | None:
        if not self.root.startswith("session:"):
            return None
        return UUID(self.root.partition(":")[2])

    def __str__(self) -> str:
        return self.root


class DeliveryPlan(ContractModel):
    """结果去哪：只有 NONE 与 PUSH；Web pull 不是投递意图（design §10.2 / §10.13）。"""

    intent: DeliveryIntent = DeliveryIntent.NONE
    #: PUSH 的冻结目标；NONE 不得携带（否则等于承诺一条不存在的投递）。
    route: DeliveryRouteInput | None = None

    @model_validator(mode="after")
    def _target_shape(self) -> Self:
        push = self.intent is DeliveryIntent.PUSH
        if push != (self.route is not None):
            raise ValueError("delivery.route must be present exactly when intent is PUSH")
        return self


class ToolPolicy(ContractModel):
    """per-job 工具面；空/`*` = 跟随 owner（design §10.9）。"""

    tools_allow: list[str] = Field(default_factory=lambda: ["*"], max_length=64)

    @field_validator("tools_allow")
    @classmethod
    def _tools_shape(cls, value: list[str]) -> list[str]:
        entries = [entry.strip() for entry in value]
        if any(not entry for entry in entries):
            raise ValueError("tools_allow entries must be non-empty")
        if len(set(entries)) != len(entries):
            raise ValueError("tools_allow entries must be unique")
        if "*" in entries and len(entries) > 1:
            raise ValueError("tools_allow '*' cannot be combined with explicit tools")
        return entries

    @property
    def follows_owner(self) -> bool:
        return self.tools_allow == ["*"]


class SchedulePlan(ContractModel):
    """一次定时任务的可执行配置：payload + session + delivery，`mode` 只读派生。"""

    payload: SchedulePayload
    session_target: SessionTarget = Field(default_factory=lambda: SessionTarget("isolated"))
    delivery: DeliveryPlan = Field(default_factory=DeliveryPlan)

    @property
    def derived_mode(self) -> ScheduleMode:
        return derive_schedule_mode(self.payload)

    def resolved_tools(self) -> ToolPolicy:
        """AGENT_TURN 的工具面来自 payload；无模型载荷没有工具。"""
        if isinstance(self.payload, AgentTurnPayload):
            return ToolPolicy(tools_allow=self.payload.tools_allow or ["*"])
        return ToolPolicy(tools_allow=[])


class ScheduleJob(SchedulePlan):
    """Job 的权威配置形状：schedule + plan；创建后由不可变 Revision 冻结。"""

    #: 用户原始自然语言，原样保留。
    instruction: str = Field(min_length=1, max_length=4000)
    recurrence: Recurrence
    context: DraftContext = Field(default_factory=DraftContext)
    #: 展示名；CREATE 可空（确认时必填或由 instruction 派生），UPDATE 缺省沿用原名。
    name: str | None = Field(default=None, min_length=1, max_length=160)


class ScheduleDraftRequest(ScheduleJob):
    """API-01：创建 Draft（CREATE 新建，UPDATE 绑定 base_config_version 做 CAS）。"""

    kind: DraftKind = DraftKind.CREATE
    base_schedule_id: UUID | None = None
    base_config_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _kind_shape(self) -> Self:
        update = self.kind is DraftKind.UPDATE
        if update != (self.base_schedule_id is not None):
            raise ValueError("UPDATE requires base_schedule_id; CREATE forbids it")
        if update != (self.base_config_version is not None):
            raise ValueError("UPDATE requires base_config_version; CREATE forbids it")
        return self


class ScheduleDiffEntry(ContractModel):
    """UPDATE 预览的一项前后差异（`field` 是载荷里的顶层键，值原样取自 Revision 与 Draft）。"""

    field: str = Field(min_length=1, max_length=64)
    before: Any = None
    after: Any = None


class SchedulePreview(ContractModel):
    """确认卡片上的事实：做什么、何时、在哪看、依赖、预算（UPDATE 另带前后差异）。"""

    #: 内部派生展示值；用户输入的只有 payload。
    mode: ScheduleMode
    payload: SchedulePayload
    session_target: SessionTarget
    what: str = Field(min_length=1)
    when: str = Field(min_length=1)
    first_fire_at: AwareDatetime
    delivery: DeliveryIntent
    tools_allow: list[str] = Field(default_factory=list)
    #: 预算来源：`PLATFORM` = 平台设置；`NONE` = 该 payload 不消费模型预算。
    budget_source: Literal["PLATFORM", "NONE"] = "NONE"
    #: Authoring-only card facts: how sure the compiler is and why it chose this plan.
    #: They never enter the frozen Job config (design §10.4 / §10.11).
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=2000)
    dependencies: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    #: 只有 UPDATE Draft 才有：CREATE 没有「之前」，空列表就是「没有旧配置可比」。
    diff: list[ScheduleDiffEntry] = Field(default_factory=list)


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
    #: Schedule 的 owner（创建者）；Console 列表按它过滤/展示，名称由 Console 的 control 源补齐。
    actor_user_id: UUID
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
    payload: SchedulePayload
    session_target: SessionTarget
    dependencies: list[str] = Field(default_factory=list)
    delivery_intent: DeliveryIntent
    tools_allow: list[str] = Field(default_factory=list)
    budget_source: Literal["PLATFORM", "NONE"] = "NONE"
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


class HistoryRetention(ContractModel):
    """历史保留提示：哪些记录还在、从哪个边界起可能已按策略回收（B-10）。

    **"空页"不等于"没发生过"**：保留清理删掉的 Fire/Execution 不会以任何假数据补位，
    因此读面必须同时给出保留口径——页面为空且 `cleaned_before` 早于用户要看的时间窗时，
    界面说的是"已按保留策略清理"，不是"这条任务从没跑过"。
    """

    fire_history_days: int = Field(ge=1)
    execution_history_days: int = Field(ge=1)
    #: 服务端算出的清理边界：早于此时刻的 Fire 可能已被回收。
    cleaned_before: AwareDatetime
    #: 该 Schedule 当前还能查到的最早一条 Fire 的计划时刻；`None` = 一条都没有。
    oldest_retained_at: AwareDatetime | None = None


class FireHistoryPage(ContractModel):
    items: list[FireItem]
    dst_events: list[DstEvent] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    retention: HistoryRetention | None = None


class TaskArtifactDeliveryRef(ContractModel):
    """产物对一个目标的交付状态（统一 Artifact 投递契约的读投影）。

    `target_key` 只用于后端定位；用户界面只展示 `target_label`（如“企微”），不暴露路由
    身份里的内部 ID。旧读投影没有标签时保留 `None`，新投影一律填写。
    """

    target_key: str = Field(min_length=1, max_length=256)
    target_label: str | None = Field(default=None, max_length=64)
    status: Literal["PENDING", "DELIVERED", "DEGRADED", "UNKNOWN", "FAILED"]
    error_code: str | None = None
    retryable: bool = True
    fallback_url: str | None = None


class TaskArtifactRef(ContractModel):
    """任务产物的不可变版本引用；下载必须带 version + checksum。"""

    artifact_id: UUID
    name: str
    media_type: str
    size: int = Field(ge=0)
    checksum: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    deliveries: list[TaskArtifactDeliveryRef] = Field(default_factory=list)


class TaskResultCounts(ContractModel):
    total: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)


class TaskChildItem(ContractModel):
    task_id: UUID
    item_key: str | None = None
    label: str
    status: TaskStatus
    error_code: str | None = None
    error_message_summary: str | None = None
    artifact_count: int = Field(default=0, ge=0)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class TaskChildPage(ContractModel):
    items: list[TaskChildItem] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    total: int = Field(default=0, ge=0)


class TaskCenterTaskItem(ContractModel):
    task_id: UUID
    agent_id: UUID
    task_type: TaskType
    skill_name: str
    status: TaskStatus
    counts: TaskResultCounts = Field(default_factory=TaskResultCounts)
    partial_failure: bool = False
    child_total: int = Field(default=0, ge=0)
    child_finished: int = Field(default=0, ge=0)
    has_artifacts: bool = False
    create_time: AwareDatetime
    finished_at: AwareDatetime | None = None


class TaskCenterTaskPage(ContractModel):
    items: list[TaskCenterTaskItem] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    total: int = Field(default=0, ge=0)


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
    #: 后台任务详情必填；定时任务既有构造点也逐步补齐。
    agent_id: UUID | None = None
    task_type: TaskType = TaskType.SKILL
    skill_name: str = ""
    parent_id: UUID | None = None
    item_key: str | None = None
    status: TaskStatus
    wait_kind: WaitKind | None = None
    wait_payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    result_artifacts: list[TaskArtifactRef] = Field(default_factory=list)
    artifact_total: int = Field(default=0, ge=0)
    counts: TaskResultCounts = Field(default_factory=TaskResultCounts)
    partial_failure: bool = False
    children: TaskChildPage = Field(default_factory=TaskChildPage)
    error_code: str | None = None
    error_message: str | None = None
    failure_class: FailureClass | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    snapshot_schema_version: int = Field(ge=3)
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


class ChannelHumanRouteRequest(ContractModel):
    """IM 入口的一条文本消息：**先生成路由裁决，再决定要不要建 Run**（design §3.2.6）。

    身份与匹配范围只来自受信调用方：actor/agent 由 Gateway 的 Console 核验给出，
    `channel/bot_id/external_user_id/external_conversation_id` 是这条消息所属的精确来源。
    刻意不接收任何渠道私有字段（reply_id、媒体令牌…），未知键直接 422。
    """

    agent_id: UUID
    platform_user_id: UUID
    #: 同源消息的重投幂等身份（入站消息 id = Worker 回答台账的幂等键）。
    message_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=4000)
    channel: str = Field(min_length=1, max_length=32)
    bot_id: str = Field(min_length=1, max_length=256)
    external_user_id: str | None = Field(default=None, max_length=256)
    external_conversation_id: str | None = Field(default=None, max_length=256)


class HumanRouteCandidate(ContractModel):
    """`CLARIFY` 时候选问题的可读事实：用户据此指定要回答哪一个任务。"""

    interaction_id: UUID
    schedule_id: UUID
    execution_id: UUID
    schedule_name: str
    question: str
    deadline_at: AwareDatetime


class ChannelHumanRouteResponse(ContractModel):
    outcome: HumanRouteOutcome
    #: `ANSWERED` / `REJECTED` 时命中的那一个提问。
    interaction_id: UUID | None = None
    #: `CLARIFY` 的完整候选集（为空表示不是澄清）。
    candidates: list[HumanRouteCandidate] = Field(default_factory=list)
    #: `REJECTED` 的稳定原因码（来自 Owner 的错误码/原因），不是自由文本。
    reason: str | None = Field(default=None, max_length=64)


class ConfirmScope(ContractModel):
    """Draft 的确认来源：一次用户动作只能确认一个 Draft。"""

    surface: ConfirmationSurface
    ref: str = Field(min_length=1, max_length=256)


#: Task 产物用途：只有 OUTPUT 是用户可下载产物；TOOL_RESULT/TRANSCRIPT 是内部留档。
ArtifactPurpose = Literal["OUTPUT", "TOOL_RESULT", "TRANSCRIPT"]


class TaskArtifactWriteRequest(ContractModel):
    """API-28：Task-owned 产物写入/追加版本。`base_version` 是 CAS，不是提示。"""

    task_execution_id: UUID
    purpose: ArtifactPurpose = "OUTPUT"
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
    #: 冻结的 workspace 归属：定时执行不接受 Task 行上的可变身份。
    workspace: dict[str, str] = Field(default_factory=dict)


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
    #: 执行超时：定时任务在 Fire Admission 时冻结，Worker 只读快照。
    execution_timeout_sec: int | None = Field(default=None, ge=1)
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
    "AgentTurnPayload",
    "AsyncWaitPayload",
    "ConfirmScope",
    "DeliveryPlan",
    "DraftContext",
    "DstEvent",
    "ExecutionDetail",
    "FireHistoryPage",
    "FireItem",
    "HumanRespondRequest",
    "HumanRespondResponse",
    "LastFireSummary",
    "OperationWatermark",
    "PAYLOAD_MODE",
    "PendingAttentionItem",
    "PendingHumanItem",
    "PendingItem",
    "PendingPage",
    "Recurrence",
    "ReminderPayload",
    "ScheduleActionRequest",
    "ScheduleDetail",
    "ScheduleDraftConfirmRequest",
    "ScheduleDraftConfirmResponse",
    "ScheduleDraftRequest",
    "ScheduleDraftResponse",
    "ScheduleDiffEntry",
    "ScheduleJob",
    "ScheduleListItem",
    "SchedulePayload",
    "SchedulePlan",
    "SchedulePolicy",
    "SchedulePreview",
    "ScheduleWindow",
    "SessionTarget",
    "SkillPlanPayload",
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
    "ToolPolicy",
    "derive_schedule_mode",
    "parse_schedule_payload",
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
