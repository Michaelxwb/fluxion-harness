"""Memory wire contracts; provenance and confirmation are separate facts."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue, StrictStr, model_validator

from .canonical import canonical_json
from .tasks import ContractModel

MEMORY_MAX_RECORDS = 1000
MEMORY_MAX_BYTES = 1024 * 1024
MEMORY_VALUE_MAX_CHARS = 512
TASK_STATE_MAX_BYTES = 2048


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("memory text cannot be blank")
    return value


MemoryKey = Annotated[StrictStr, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")]
MemoryValue = Annotated[
    StrictStr, Field(min_length=1, max_length=MEMORY_VALUE_MAX_CHARS), AfterValidator(_non_blank)
]
TaskItem = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(_non_blank)]
NonNegativeVersion = Annotated[int, Field(strict=True, ge=0)]
PositiveVersion = Annotated[int, Field(strict=True, ge=1)]


class MemoryAuthor(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    AGENT = "AGENT"


class MemoryCategory(StrEnum):
    PREFERENCE = "PREFERENCE"
    WORK_STYLE = "WORK_STYLE"
    EXPLICIT = "EXPLICIT"
    TASK_STATE = "TASK_STATE"


class MemoryRetirementReason(StrEnum):
    USER_DELETE = "USER_DELETE"
    ADMIN_DELETE = "ADMIN_DELETE"
    EXPIRE = "EXPIRE"
    QUOTA_EVICT = "QUOTA_EVICT"
    TASK_COMPLETE = "TASK_COMPLETE"


class FactContent(ContractModel):
    value: MemoryValue


class TaskStateContent(ContractModel):
    title: StrictStr = Field(min_length=1, max_length=80)
    goal: StrictStr = Field(min_length=1, max_length=256)
    constraints: list[TaskItem] = Field(max_length=5)
    open_items: list[TaskItem] = Field(max_length=5)
    next_action: StrictStr = Field(max_length=256)
    root_conversation_id: UUID
    source_conversation_id: UUID

    @model_validator(mode="after")
    def _bounded_content(self) -> Self:
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > TASK_STATE_MAX_BYTES:
            raise ValueError("TASK_STATE canonical UTF-8 content exceeds 2048 bytes")
        return self


def task_memory_key(root_conversation_id: UUID) -> str:
    return f"task.{root_conversation_id.hex}"


class RememberMemoryInput(ContractModel):
    """Model input contains neither identity nor authorization."""

    memory_key: MemoryKey
    value: MemoryValue
    category: Literal["PREFERENCE", "WORK_STYLE", "EXPLICIT"]
    source_quote: MemoryValue | None = None

    @model_validator(mode="after")
    def _ordinary_namespace(self) -> Self:
        if self.memory_key.startswith("task."):
            raise ValueError("task.* is reserved for TASK_STATE")
        return self


class RecallMemoryInput(ContractModel):
    query: StrictStr | None = Field(default=None, max_length=512)
    category: MemoryCategory | None = None
    memory_key: MemoryKey | None = None
    limit: Annotated[int, Field(strict=True, ge=0, le=20)] | None = None


class ForgetMemoryInput(ContractModel):
    memory_key: MemoryKey
    expected_version: PositiveVersion


class MemoryResumeOption(ContractModel):
    number: Annotated[int, Field(strict=True, ge=1, le=20)]
    memory_key: MemoryKey
    version: PositiveVersion
    root_conversation_id: UUID
    title: StrictStr = Field(min_length=1, max_length=80)
    candidate_hash: StrictStr = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _root_identity(self) -> Self:
        if self.memory_key != task_memory_key(self.root_conversation_id):
            raise ValueError("resume option must retain the original task root")
        return self


class MemoryResumeOptionsBody(ContractModel):
    user_id: UUID
    source_user_event_id: UUID
    theme: StrictStr | None
    items: list[MemoryResumeOption] = Field(min_length=1, max_length=20)
    expires_at: AwareDatetime


class MemoryResumeOptions(MemoryResumeOptionsBody):
    options_hash: StrictStr = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _canonical_options(self) -> Self:
        if [row.number for row in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("resume options must have consecutive Runtime numbers")
        if len({row.memory_key for row in self.items}) != len(self.items):
            raise ValueError("resume options must have unique task keys")
        if self.options_hash != memory_hash(self.model_dump(mode="json", exclude={"options_hash"})):
            raise ValueError("resume options hash mismatch")
        return self


class MemoryResumeRef(ContractModel):
    memory_key: MemoryKey
    expected_version: PositiveVersion
    root_conversation_id: UUID
    options_event_id: UUID
    resolution_event_id: UUID
    source_user_event_id: UUID
    candidate_hash: StrictStr = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _root_identity(self) -> Self:
        if self.memory_key != task_memory_key(self.root_conversation_id):
            raise ValueError("resume reference must retain the original task root")
        return self


class MemoryCandidate(ContractModel):
    memory_key: MemoryKey
    category: MemoryCategory
    content: FactContent | TaskStateContent

    @model_validator(mode="after")
    def _content_matches_category(self) -> Self:
        if self.category is MemoryCategory.TASK_STATE:
            if not isinstance(self.content, TaskStateContent):
                raise ValueError("TASK_STATE requires task content")
            if self.memory_key != task_memory_key(self.content.root_conversation_id):
                raise ValueError("TASK_STATE key must reference its root conversation")
        elif not isinstance(self.content, FactContent) or self.memory_key.startswith("task."):
            raise ValueError("ordinary facts cannot use task content or namespace")
        return self


class MemorySourceEvidence(ContractModel):
    """Constructed by Runtime from a real submission, never from tool input."""

    source_event_id: UUID
    source_seq: PositiveVersion
    source_scope_revision: NonNegativeVersion
    source_quote: MemoryValue | None = None


class MemoryConfirmationProposal(ContractModel):
    tenant_id: StrictStr = Field(min_length=1)
    user_id: UUID
    run_id: UUID
    submission_id: UUID
    candidate: RememberMemoryInput
    candidate_hash: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_version: PositiveVersion | None
    forget_epoch: NonNegativeVersion
    evidence: MemorySourceEvidence
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _candidate_hash_matches(self) -> Self:
        if self.candidate_hash != memory_hash(self.candidate.model_dump(mode="json")):
            raise ValueError("confirmation candidate hash does not match")
        return self


class MemoryConfirmationInput(ContractModel):
    proposal_id: UUID
    action: Literal["CONFIRM", "REJECT"]


def memory_hash(value: JsonValue) -> str:
    """Use the shared canonical JSON primitive, including strict JSON checks."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def memory_content_bytes(content: JsonValue, source_quote: str | None) -> int:
    """Fact quota counts canonical content and quote, including JSON null."""
    return len(canonical_json(content).encode("utf-8")) + len(canonical_json(source_quote).encode("utf-8"))
