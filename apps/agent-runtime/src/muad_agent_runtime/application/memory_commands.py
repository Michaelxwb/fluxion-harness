"""Runtime-owned command DTOs; tool arguments never construct write authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Self
from uuid import UUID

from muad_contracts.memory import (
    MemoryCandidate,
    MemoryKey,
    MemoryRetirementReason,
    MemorySourceEvidence,
    NonNegativeVersion,
    PositiveVersion,
)
from muad_contracts.memory_admin import MemoryCommandResponse
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from pydantic import AwareDatetime, Field, StrictBool, StrictStr, model_validator

MemoryActor = Literal["USER", "ADMIN", "AGENT", "SYSTEM"]
MemoryAction = Literal["UPSERT", "DELETE", "CLEAR", "ENABLE", "DISABLE", "RESTORE", "RETIRE"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryWriteContext:
    tenant_id: str
    user_id: UUID
    actor_type: MemoryActor
    actor_id: str
    settings: MemoryPolicySettings = field(default_factory=MemoryPolicySettings)
    credential_canaries: tuple[str, ...] = ()
    source_run_id: UUID | None = None
    conversation_id: UUID | None = None
    evidence: MemorySourceEvidence | None = None
    confirmation_event_id: UUID | None = None
    expected_forget_epoch: int | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in ("USER", "ADMIN", "AGENT", "SYSTEM"):
            raise ValueError("invalid memory actor")
        if not self.tenant_id or len(self.tenant_id) > 64 or len(self.actor_id) > 128:
            raise ValueError("invalid memory scope")
        if self.expected_forget_epoch is not None and self.expected_forget_epoch < 0:
            raise ValueError("invalid forget epoch")


class MemoryMutation(ContractModel):
    action: MemoryAction
    memory_key: MemoryKey | None = None
    candidate: MemoryCandidate | None = None
    expected_version: PositiveVersion | None = None
    restore: StrictBool = False
    enable: StrictBool = False
    expires_at: AwareDatetime | None = None
    retirement_reason: MemoryRetirementReason | None = None

    @property
    def key(self) -> str | None:
        return self.candidate.memory_key if self.candidate is not None else self.memory_key

    @property
    def expiry_provided(self) -> bool:
        return "expires_at" in self.model_fields_set

    @model_validator(mode="after")
    def _action_shape(self) -> Self:
        if self.action == "UPSERT":
            if self.candidate is None or self.memory_key is not None:
                raise ValueError("UPSERT requires a candidate")
        elif self.candidate is not None:
            raise ValueError("only UPSERT accepts a candidate")
        elif self.action == "CLEAR":
            if self.memory_key is not None or self.expected_version is not None:
                raise ValueError("CLEAR is scoped to the current user")
        elif self.memory_key is None or self.expected_version is None:
            raise ValueError("mutation requires key and expected_version")
        if self.action == "RETIRE" and self.retirement_reason is None:
            raise ValueError("RETIRE requires a reason")
        if self.action != "RETIRE" and self.retirement_reason is not None:
            raise ValueError("retirement reason is server controlled")
        if self.restore and self.action != "UPSERT":
            raise ValueError("restore is only an explicit UPSERT option")
        if self.enable and self.action not in ("UPSERT", "RESTORE"):
            raise ValueError("enable is only an UPSERT/RESTORE option")
        if self.expiry_provided and self.action not in ("UPSERT", "ENABLE", "RESTORE"):
            raise ValueError("expiry is only an UPSERT/ENABLE/RESTORE option")
        return self


class MemoryCommand(ContractModel):
    command_id: StrictStr = Field(min_length=1, max_length=256)
    endpoint: StrictStr = Field(min_length=1, max_length=128)
    mutations: list[MemoryMutation] = Field(min_length=1, max_length=1000)
    expected_scope_revision: NonNegativeVersion | None = None

    @model_validator(mode="after")
    def _unique_targets(self) -> Self:
        if any(item.action == "CLEAR" for item in self.mutations):
            if len(self.mutations) != 1:
                raise ValueError("CLEAR is a complete batch")
        elif len({item.key for item in self.mutations}) != len(self.mutations):
            raise ValueError("one explicit mutation per key")
        return self


@dataclass(frozen=True, slots=True)
class MemoryCommandOutcome:
    response: MemoryCommandResponse
    replayed: bool
