from enum import StrEnum

# 通道名的**唯一**声明处。契约字段是**开放的 string**（新增通道只写适配器，不改这里：
# 路由由 `ChannelRegistry` 的适配器名决定）；下面列出的是内建通道名，供默认值/文档与
# 机检（一处声明）使用。放在 leaves 模块（仅依赖标准库），避免 channel ↔ tasks 的循环导入。
# 机检：tests/test_attachment_contract.py::test_b07_channel_enum_is_defined_in_exactly_one_place
BUILTIN_CHANNEL_NAMES: tuple[str, ...] = ("WECOM", "WEBCHAT")
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
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
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
    SKILL = "SKILL"
    BATCH = "BATCH"


class TriggerType(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    SCHEDULED = "SCHEDULED"


class ScheduleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    # ONCE 错过触发时间后的终态：completed_at / next_fire_at 均为 NULL，不可恢复。
    MISSED = "MISSED"


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
