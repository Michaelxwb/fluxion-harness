from enum import StrEnum


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
