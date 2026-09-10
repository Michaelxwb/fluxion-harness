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


class ExecutionSnapshot(BaseModel):
    service_release_ref: str
    service_content_hash: str
    agent_release_ref: str | None = None
    agent_revision: int | None = None
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
    service_release_ref: str
    resource_scope: dict[str, object] = Field(default_factory=dict)
    input: dict[str, object] = Field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.PENDING
    next_run_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempt: int = 0
    max_attempts: int = 3
    idempotency_key: str
    delivery_route_id: UUID | None = None
    trace_id: str | None = None
