"""Shared policy contracts; no transport, datastore or application imports."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .egress_budget import PositiveCount, PositiveSeconds


class SharedPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SharedRule(SharedPolicyModel):
    id: str = Field(min_length=1, pattern=r"^\S+$")
    target_instance_id: str = Field(min_length=1, pattern=r"^\S+$")
    route_id: str = Field(min_length=1)
    max_requests: PositiveCount | None = None
    window_requests: PositiveCount | None = None
    window_seconds: PositiveSeconds | None = None
    max_inflight: PositiveCount | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if (self.window_requests is None) != (self.window_seconds is None):
            raise ValueError("window fields must be specified together")
        if all(value is None for value in (
            self.max_requests, self.window_requests, self.max_inflight,
        )):
            raise ValueError("a shared rule must specify at least one limit")
        if not self.route_id.strip():
            raise ValueError("route_id must not be blank")
        return self


@dataclass(frozen=True, slots=True)
class RuleSelection:
    coverage: Literal["SUPPORTED", "UNSUPPORTED"]
    rule_ids: tuple[str, ...]


class SharedEgressPolicy(SharedPolicyModel):
    revision: int = Field(default=0, ge=0, strict=True)
    rules: tuple[SharedRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate shared rule id")
        return self

    @property
    def enabled(self) -> bool:
        return bool(self.rules)

    @classmethod
    def from_runtime_config(cls, value: object) -> Self:
        """Parse a trusted host configuration, never a caller's request metadata."""
        if not isinstance(value, Mapping):
            raise ValueError("shared egress policy must be an object")
        return cls.model_validate(value)

    def select(self, target_instance_id: str, route_id: str, *, supported: bool) -> RuleSelection:
        if not supported:
            return RuleSelection("UNSUPPORTED", ())
        matches = tuple(rule.id for rule in self.rules
                        if rule.target_instance_id == target_instance_id and rule.route_id == route_id)
        return RuleSelection("SUPPORTED", matches)


@dataclass(frozen=True, slots=True)
class TrustedInvocation:
    """Host-constructed identity. This value object does not authenticate callers."""
    execution_id: str
    invocation_id: str
    segment_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (
            self.execution_id, self.invocation_id, self.segment_id,
        )):
            raise ValueError("execution, invocation and segment IDs are required")

    def with_segment(self, segment_id: str) -> TrustedInvocation:
        return TrustedInvocation(self.execution_id, self.invocation_id, segment_id)
