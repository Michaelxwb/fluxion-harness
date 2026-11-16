from enum import StrEnum

# 通道名的**唯一**声明处。契约字段是**开放的 string**（新增通道只写适配器，不改这里：
# 路由由 `ChannelRegistry` 的适配器名决定）；下面列出的是内建通道名，供默认值/文档与
# 机检（一处声明）使用。放在 leaves 模块（仅依赖标准库），避免 channel ↔ tasks 的循环导入。
# 机检：tests/test_attachment_contract.py::test_b07_channel_enum_is_defined_in_exactly_one_place
BUILTIN_CHANNEL_NAMES: tuple[str, ...] = ("WECOM", "WEBCHAT", "MSSWWEB")
#: 兼容别名：类型上是开放 string（WECOM=企微机器人；WEBCHAT=通用网页稳定入口）。
ChannelName = str


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_INPUT = "WAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    """Task Execution 状态（`task.task_execution.status`）。

    外部等待不再是单一 `WAITING`：`WAITING_EXTERNAL`（异步操作/外部轮询/子任务聚合，
    细类见 `WaitKind`）与 `WAITING_HUMAN`（等待用户回答）语义不同，取消与续接规则也不同。
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CompletionMode(StrEnum):
    JOIN = "JOIN"
    DETACH = "DETACH"


class TerminalStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OperationStatus(StrEnum):
    SUBMIT_PENDING = "SUBMIT_PENDING"
    SUBMITTED = "SUBMITTED"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    RUNNING = "RUNNING"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    MATERIALIZED = "MATERIALIZED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LATE = "LATE"


class ControlOutboxStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class ControlCommand(StrEnum):
    SUBMIT = "SUBMIT"
    CANCEL_OPERATION = "CANCEL_OPERATION"


class InboxMaterializationState(StrEnum):
    PENDING = "PENDING"
    RECEIVED = "RECEIVED"
    MATERIALIZED = "MATERIALIZED"
    LATE = "LATE"
    FAILED = "FAILED"


class WaitReason(StrEnum):
    SUBMISSION = "SUBMISSION"
    TASK_RESULT = "TASK_RESULT"
    RESUME_READY = "RESUME_READY"


class OperationErrorPhase(StrEnum):
    SUBMIT = "SUBMIT"
    EXECUTE = "EXECUTE"


class TaskType(StrEnum):
    """执行类型。BATCH 是**单次执行内部**的父子扇出类型，不是 Schedule 模式。"""

    SKILL = "SKILL"
    REMINDER = "REMINDER"
    AGENTIC = "AGENTIC"
    BATCH = "BATCH"


class TriggerType(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    SCHEDULED = "SCHEDULED"


class ScheduleStatus(StrEnum):
    """Schedule 生命周期。终态只有 `ENDED`/`CANCELLED`，原因在 `end_reason`。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class ScheduleEndReason(StrEnum):
    """ENDED 的原因（`task.task_schedule.end_reason`）。"""

    #: ONCE 已执行 / CRON 到达 until。
    COMPLETED = "COMPLETED"
    #: ONCE 错过触发时间，不补发。
    MISSED = "MISSED"
    #: max_occurrences 用尽。
    EXHAUSTED = "EXHAUSTED"
    #: 恢复时的 Admission 判定为永久失败。
    ADMISSION_FAILED = "ADMISSION_FAILED"
    USER_CANCELLED = "USER_CANCELLED"


class ScheduleMode(StrEnum):
    REMINDER = "REMINDER"
    SKILL = "SKILL"
    AGENTIC = "AGENTIC"


class RecurrenceKind(StrEnum):
    CRON = "CRON"
    ONCE = "ONCE"


class ConcurrencyPolicy(StrEnum):
    #: 上一次根执行未结束时跳过本次 occurrence。
    SKIP_IF_RUNNING = "SKIP_IF_RUNNING"
    #: 排队等待，按 scheduled_at FIFO 领取。
    QUEUE = "QUEUE"
    #: 允许并行，上限由 max_concurrency 决定。
    ALLOW_PARALLEL = "ALLOW_PARALLEL"


class DstGapPolicy(StrEnum):
    #: P0 唯一策略：本地壁钟落在 gap 里的 occurrence 跳过并发事件。
    SKIP = "SKIP"


class DstFoldPolicy(StrEnum):
    #: P0 唯一策略：fold 的重复本地时刻只按第一次触发。
    FIRST = "FIRST"


class DstResolution(StrEnum):
    """Fire 的 DST 解析结果（`schedule_fire.dst_resolution`）。"""

    FOLD_FIRST = "FOLD_FIRST"


class DstEventType(StrEnum):
    """不产生 Fire 的 DST 事件（写 SCHEDULE 事件，由 Fire 历史接口一并返回）。"""

    DST_GAP_SKIPPED = "DST_GAP_SKIPPED"
    DST_FOLD_SECOND_SKIPPED = "DST_FOLD_SECOND_SKIPPED"


class FireStatus(StrEnum):
    """一次 occurrence 的处理结果（`task.schedule_fire.status`）。"""

    #: 已写 Fire，尚未创建 Execution。
    CREATED = "CREATED"
    EXECUTION_CREATED = "EXECUTION_CREATED"
    SKIPPED = "SKIPPED"
    FAILED_ADMISSION = "FAILED_ADMISSION"
    CANCELLED = "CANCELLED"


class SkipReason(StrEnum):
    """跳过本次 occurrence 的原因（`skip_reason`）。"""

    #: SKIP_IF_RUNNING 且有未结束的根执行。
    OVERLAP = "OVERLAP"
    #: 错过区间：只处理最近一次，其余计入 missed_count。
    MISFIRE = "MISFIRE"
    QUOTA = "QUOTA"
    #: 更新时取消了尚未开始的 occurrence。
    PAUSED_BY_UPDATE = "PAUSED_BY_UPDATE"


class WaitKind(StrEnum):
    """WAITING_EXTERNAL 的细类（`wait_kind`；WAITING_HUMAN 不用本枚举）。"""

    ASYNC_OPERATION = "ASYNC_OPERATION"
    EXTERNAL_POLL = "EXTERNAL_POLL"
    BATCH_FANIN = "BATCH_FANIN"


class FailureClass(StrEnum):
    BUSINESS = "BUSINESS"
    CONFIG = "CONFIG"
    TIMEOUT = "TIMEOUT"
    HUMAN_TIMEOUT = "HUMAN_TIMEOUT"
    ASYNC_FAILED = "ASYNC_FAILED"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"


class CancelScope(StrEnum):
    """取消范围：只停未来，或终止全部（含进行中/等待中）。"""

    FUTURE_ONLY = "FUTURE_ONLY"
    ALL_ACTIVE = "ALL_ACTIVE"


class DraftKind(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"


class DraftStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ConfirmationSurface(StrEnum):
    """Draft 的确认来源 surface（一次用户动作只能确认一个 Draft）。"""

    WEBCHAT = "WEBCHAT"
    WECOM = "WECOM"
    MSSWWEB = "MSSWWEB"


class DeliveryIntent(StrEnum):
    """Revision 的投递意图；WebChat/MSSWWEB 为 NONE（只 pull）。"""

    PUSH = "PUSH"
    NONE = "NONE"


class MemoryMode(StrEnum):
    """定时任务只读记忆：准入时冻结注入块，任务不得写长期记忆。"""

    READ_ONLY = "READ_ONLY"


class OwnerType(StrEnum):
    """Runtime 内部资源的 owner：交互式 Run 或 Task Execution。"""

    RUN = "RUN"
    TASK = "TASK"


class HumanInteractionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class HumanRouteOutcome(StrEnum):
    """聊天入站消息被判定成的去向（design §3.2.6）：只有 NONE 才允许新建 Run。

    - `NONE`：不是 Human 回答，按普通聊天继续；
    - `ANSWERED`：恰好一个 OPEN 提问，回答已写回并续接该 Execution；
    - `CLARIFY`：同一 user+agent+会话有多个 OPEN 提问，必须由用户指定，绝不按新旧挑选；
    - `REJECTED`：命中了一个提问但回答未被接受（已超时/已取消/格式不符），状态不变。
    """

    NONE = "NONE"
    ANSWERED = "ANSWERED"
    CLARIFY = "CLARIFY"
    REJECTED = "REJECTED"


class DeliveryMode(StrEnum):
    FINAL_ONLY = "FINAL_ONLY"
    NONE = "NONE"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    NONE = "NONE"


class SkillExecutionMode(StrEnum):
    SYNC = "SYNC"
    ASYNC = "ASYNC"
    AUTO = "AUTO"


class SkillSource(StrEnum):
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"
    USER = "USER"


class SkillReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class UserScope(StrEnum):
    ALL = "ALL"
    SELECTED = "SELECTED"


class CredentialMode(StrEnum):
    USER_ONLY = "USER_ONLY"
    SHARED_ONLY = "SHARED_ONLY"
    USER_THEN_SHARED = "USER_THEN_SHARED"
    NONE = "NONE"


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"


class InterruptStatus(StrEnum):
    WAITING = "WAITING"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class ArtifactValidationStatus(StrEnum):
    READY = "READY"
    REJECTED = "REJECTED"


#: 通道缺省值：与 `control.bot_account.channel` 的历史默认（server_default 'WECOM'）同源。
#: 共享形状（如 `BotSnapshotItem`）用它兜底；生产者（Console）仍必须按实际通道输出。
DEFAULT_CHANNEL: ChannelName = "WECOM"

#: 有真实主动投递能力的机器人通道（渠道词表属类型定义处，见
#: `tests/architecture/test_channel_neutrality.py` 的 `ALLOWED_SURFACES`）：Run 终态/待确认在这类
#: 通道写 `PENDING` 出站意图，其余（站内入口）写 `NOT_REQUIRED`。Runtime 只消费本常量，不在
#: 核心域按通道分支。
OUTBOUND_CHANNELS: frozenset[ChannelName] = frozenset({"WECOM"})

#: 出站由**本进程的持久会话流**完成的通道（站内入口）：产物不经过 IM 出站管道，直接落目标
#: 会话的 canonical 流（`artifact.created`），由该会话的订阅面与历史面读取同一事实。
#: 与 `OUTBOUND_CHANNELS` 一起覆盖内建通道；不属于任一组的未知通道必须显式拒绝，不猜。
LOCAL_STREAM_CHANNELS: frozenset[ChannelName] = frozenset({"WEBCHAT", "MSSWWEB"})
