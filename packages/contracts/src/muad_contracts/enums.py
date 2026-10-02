from enum import StrEnum
from typing import Literal

# 通道枚举的**唯一**定义处：新增通道只改这一行，其余位置一律引用本别名。
# 放在 leaves 模块（仅依赖标准库），避免 channel ↔ tasks 的循环导入。
# 机检：tests/test_attachment_contract.py::test_b07_channel_enum_is_defined_in_exactly_one_place
ChannelName = Literal["WECOM"]


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
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
