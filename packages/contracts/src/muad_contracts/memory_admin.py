"""Administrative transport carries an audit actor, never caller-selected authorship."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from .memory import (
    PositiveVersion,
)
from .tasks import ContractModel

MemoryResultStatus = Literal[
    "CREATED", "UPDATED", "DELETED", "ENABLED", "DISABLED", "RESTORED", "RETIRED", "UNCHANGED", "SKIPPED"
]
MemoryStatus = Literal["ACTIVE", "DISABLED", "EXPIRED", "DELETED"]
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
    create_time: datetime


# ---------------------------------------------------------------------------
# V2 admin transport models. Runtime route wiring is completed with the
# Console integration TASK; these models freeze the wire shape now.
# ---------------------------------------------------------------------------

from .memory import (  # noqa: E402
    MemoryItem,
    ScopeState,
)


class MemoryAdminItem(MemoryItem):
    status: MemoryStatus
    create_time: datetime
    update_time: datetime


class MemoryConflictItem(ContractModel):
    id: UUID
    memory_key: str
    predicate: str
    scope_state: ScopeState
    title: str
    summary: str
    content: dict[str, JsonValue]
    base_version: int
    forget_epoch: int
    reason: str
    create_time: datetime


class MemoryCommandRequest(ContractModel):
    #: Console 用会话账号覆盖；Runtime 侧必须非空。
    actor_id: UUID | None = None
    action: Literal[
        "CREATE", "UPDATE", "DELETE", "RESTORE", "ENABLE", "DISABLE", "CLEAR", "RESOLVE", "REJECT"
    ]
    candidate: dict[str, JsonValue] | None = None
    memory_id: UUID | None = None
    candidate_id: UUID | None = None
    expected_version: PositiveVersion | None = None
    updates: dict[str, JsonValue] | None = None
