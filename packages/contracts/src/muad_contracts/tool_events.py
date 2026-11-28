"""Versioned tool-event payloads shared by Runtime canonical events and Worker task events."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TOOL_EVENT_SCHEMA_VERSION = 2

ArtifactRoleValue = str


class _ToolEventModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolEventArtifact(_ToolEventModel):
    artifact_id: str
    role: ArtifactRoleValue
    media_type: str
    filename: str | None = None
    checksum: str | None = None
    size: int | None = None


class ToolEventFollowUp(_ToolEventModel):
    kind: str
    artifact_id: str | None = None
    media_type: str | None = None
    text: str | None = None


class ToolEventOutcome(_ToolEventModel):
    business_status: str
    failure_layer: str
    error_code: str | None = None
    retryable: bool = False
    agent_disposition: Literal["CONTINUE", "WAIT", "STOP"] = "CONTINUE"
    retry_disposition: Literal["ALLOW", "WAIT_THEN_RETRY", "BLOCK"] = "ALLOW"
    scope: Literal["CALL", "OPERATION", "RUN", "DELIVERY"] = "CALL"
    sent: bool = False
    outcome_known: bool = True
    evidence_status: Literal["NONE", "PARTIAL", "CONFIRMED", "UNKNOWN"] = "CONFIRMED"
    retry_after: str | None = None
    security_disposition: Literal["NORMAL", "DENIED", "VIOLATION"] = "NORMAL"
    boundary_kind: Literal[
        "NONE", "TIME", "OUTPUT", "RESOURCE", "TERMINATION", "CONTEXT", "OBSERVABILITY"
    ] = "NONE"
    stop_confirmed: bool = True
    evidence_preview: str = ""
    exit_code: int | None = None
    execution_status: str | None = None
    review_progress: tuple[str, int, int] | None = None
    customer_options: tuple[dict[str, str], ...] = ()
    #: Pre-dispatch gate attribution; only present on a retry-gated call.
    gate_reason: Literal[
        "BLOCKED_ATTEMPT", "UNVERIFIED_WRITE", "RETRY_GUARDED", "FAILURE_UNRESOLVED"
    ] | None = None
    source_tool_call_id: str | None = None
    #: Verified repair owner from the shared failure mapping.
    action_owner: Literal["USER", "ADMIN", "NONE"] | None = None
    #: Whether the shared mapping actually matched this code.
    hint_known: bool | None = None


class AssistantToolCallEvent(_ToolEventModel):
    id: str
    name: str
    registry_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantTurnEvent(_ToolEventModel):
    schema_version: int = TOOL_EVENT_SCHEMA_VERSION
    text: str = ""
    reasoning_content: str | None = None
    tool_calls: list[AssistantToolCallEvent] = Field(default_factory=list)


class PreparedCallEvent(_ToolEventModel):
    schema_version: int = TOOL_EVENT_SCHEMA_VERSION
    state: Literal["STARTED"] = "STARTED"
    tool_call_id: str
    name: str
    model_name: str
    args_hash: str
    args_preview: dict[str, Any] = Field(default_factory=dict)
    source: str
    skill_id: str | None = None
    skill_key: str | None = None
    tool_name: str | None = None
    execution_mode: str | None = None
    retry_safety: str
    effect: str = "EXTERNAL"
    write_targets: tuple[str, ...] = ()
    write_operation_key: str | None = None


class ToolCompletedEvent(_ToolEventModel):
    schema_version: int = TOOL_EVENT_SCHEMA_VERSION
    state: Literal["COMPLETED"] = "COMPLETED"
    tool_call_id: str
    tool_name: str
    status: str
    outcome: ToolEventOutcome
    content: str
    artifacts: list[ToolEventArtifact] = Field(default_factory=list)
    follow_ups: list[ToolEventFollowUp] = Field(default_factory=list)
    handler_entered: bool | None = None
    attempt: str | None = None


__all__ = [
    "TOOL_EVENT_SCHEMA_VERSION",
    "AssistantToolCallEvent",
    "AssistantTurnEvent",
    "PreparedCallEvent",
    "ToolCompletedEvent",
    "ToolEventArtifact",
    "ToolEventFollowUp",
    "ToolEventOutcome",
]
