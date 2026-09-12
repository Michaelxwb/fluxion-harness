from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_HUMAN = "WAITING_HUMAN"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class ExecutionSnapshotSource(StrEnum):
    FORMAL = "FORMAL"
    TEST = "TEST"


class ExecutionSnapshot(BaseModel):
    """Identity + frozen projection of one snapshot row (module 05 §3.3).

    Identity columns (service/release/source/hash) stay queryable; the frozen
    business projection itself lives in ``snapshot_json`` so a running execution
    never re-reads current configuration (RULE-SVC-03).
    """

    service_id: UUID | None = None
    source: ExecutionSnapshotSource = ExecutionSnapshotSource.FORMAL
    service_release_id: UUID | None = None
    draft_revision: int | None = None
    test_mode: str | None = None
    content_hash: str
    snapshot_schema_version: int = 1
    snapshot_json: dict[str, object] = Field(default_factory=dict)
    snapshot_ref: str | None = None

    # Convenience projections of snapshot_json, kept for readers that need the
    # frozen business answers without unpacking the dict.
    resource_scope_type: str | None = None
    resource_scope_schema_hash: str | None = None
    skill_artifacts: list[str] = Field(default_factory=list)
    knowledge_bindings: list[str] = Field(default_factory=list)
    capability_contracts: list[str] = Field(default_factory=list)
    model_config_snapshot: dict[str, object] = Field(default_factory=dict)
    execution_spec: dict[str, object] = Field(default_factory=dict)


class ServiceExecution(BaseModel):
    id: UUID
    actor_user_id: UUID
    service_id: UUID | None = None
    service_release_id: UUID | None = None
    snapshot_id: UUID | None = None
    resource_scope_json: dict[str, object] = Field(default_factory=dict)
    input_json: dict[str, object] = Field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.PENDING
    next_run_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempt: int = 0
    max_attempts: int = 3
    idempotency_key: str
    delivery_route_id: UUID | None = None
    trace_id: str | None = None
