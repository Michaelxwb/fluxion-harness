"""Declarations and host-owned facts for executable Skill results."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillResultProfile(StrEnum):
    SKILL_REVIEW = "skill-review"
    CUSTOMER_DEVICES = "customer-devices"


class SkillResultContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(strict=True, ge=1, le=1)
    profile: SkillResultProfile


class FrozenSkillResultContract(SkillResultContract):
    """A validated declaration tied to the admitted archive and exact entrypoint."""

    checksum: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    entrypoint: str = Field(min_length=1, max_length=1024)

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            value == "."
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or any(character in value for character in ("\\", "\x00", ":"))
        ):
            raise ValueError("entrypoint must be a normalized path in the Skill package")
        return value


def frozen_skill_result_contract(
    metadata: Mapping[str, object], *, checksum: str
) -> FrozenSkillResultContract | None:
    if "result_contract" not in metadata:
        return None
    contract = FrozenSkillResultContract.model_validate(metadata["result_contract"])
    if contract.checksum != checksum:
        raise ValueError("Skill result contract checksum differs from frozen artifact")
    return contract


class SkillCallStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    DENIED = "DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


class SkillBusinessConclusion(StrEnum):
    REVIEW_PASSED = "REVIEW_PASSED"
    REVIEW_FINDINGS = "REVIEW_FINDINGS"
    NO_DEVICES = "NO_DEVICES"
    NEEDS_INPUT = "NEEDS_INPUT"
    SUCCEEDED = "SUCCEEDED"


class FrozenReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    draft_id: UUID
    revision: int = Field(ge=1, strict=True)
    checksum: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
