"""Administrative transport carries an audit actor, never caller-selected authorship."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, JsonValue, StrictBool, StrictStr, model_validator

from .memory import (
    MemoryAuthor,
    MemoryCandidate,
    MemoryKey,
    MemoryValue,
    PositiveVersion,
    RememberMemoryInput,
)
from .tasks import ContractModel

MemoryResultStatus = Literal[
    "CREATED", "UPDATED", "DELETED", "ENABLED", "DISABLED", "RESTORED", "RETIRED", "UNCHANGED", "SKIPPED"
]
MemoryStatus = Literal["ACTIVE", "DISABLED", "EXPIRED"]
AdminAction = Literal["UPSERT", "DELETE", "CLEAR", "ENABLE", "DISABLE", "RESTORE"]


class MemoryMutationResult(ContractModel):
    memory_key: str
    id: UUID | None
    version: int | None
    status: MemoryResultStatus
    reason: str | None = None


class MemoryCommandResponse(ContractModel):
    command_id: str
    scope_revision: int
    forget_epoch: int
    changed_count: int
    status: Literal["APPLIED", "UNCHANGED", "STALE"]
    items: list[MemoryMutationResult]


class MemoryAdminMutation(ContractModel):
    action: AdminAction
    memory_key: MemoryKey | None = None
    memory_id: UUID | None = None
    category: Literal["PREFERENCE", "WORK_STYLE", "EXPLICIT"] | None = None
    value: MemoryValue | None = None
    expected_version: PositiveVersion | None = None
    restore: StrictBool = False
    enable: StrictBool = False
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        allowed = {
            "UPSERT": {
                "action",
                "memory_key",
                "category",
                "value",
                "expected_version",
                "restore",
                "enable",
                "expires_at",
            },
            "DELETE": {"action", "memory_key", "memory_id", "expected_version"},
            "CLEAR": {"action"},
            "ENABLE": {"action", "memory_key", "expected_version", "expires_at"},
            "DISABLE": {"action", "memory_key", "expected_version"},
            "RESTORE": {"action", "memory_key", "expected_version", "enable", "expires_at"},
        }[self.action]
        if self.model_fields_set - allowed:
            raise ValueError("fields do not belong to this action")
        if self.action == "UPSERT":
            RememberMemoryInput.model_validate(
                {"memory_key": self.memory_key, "category": self.category, "value": self.value}
            )
        elif self.action != "CLEAR":
            if self.expected_version is None:
                raise ValueError("expected_version required")
            if self.action == "DELETE":
                if (self.memory_key is None) == (self.memory_id is None):
                    raise ValueError("DELETE requires exactly one key or id")
            elif self.memory_key is None:
                raise ValueError("memory_key required")
        return self


class MemoryAdminRequest(ContractModel):
    actor_id: UUID
    mutation: MemoryAdminMutation


class MemoryAdminItem(MemoryCandidate):
    id: UUID
    author: MemoryAuthor
    version: PositiveVersion
    enabled: StrictBool
    explicit_request: StrictBool
    confirmed_count: int
    status: MemoryStatus
    source_ref: StrictStr | None
    source_quote: StrictStr | None
    expires_at: datetime | None
    last_confirmed_at: datetime | None
    create_time: datetime
    update_time: datetime


class MemoryHistoryItem(ContractModel):
    id: UUID
    origin_id: UUID
    memory_key: str
    version: int
    change_reason: str
    actor_type: str
    actor_id: str | None
    command_id: str
    source_run_id: UUID | None
    after: dict[str, JsonValue]
    legacy_snapshots: list[dict[str, JsonValue]] | None
    create_time: datetime
