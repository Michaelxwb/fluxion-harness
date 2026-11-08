"""Typed execution-local egress policy; no counters, transport or persistence."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveCount = Annotated[int, Field(strict=True, gt=0)]
PositiveSeconds = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


class BudgetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class LimitOverrides(BudgetModel):
    max_requests: PositiveCount | None = None
    max_inflight: PositiveCount | None = None
    window_requests: PositiveCount | None = None
    window_seconds: PositiveSeconds | None = None


class RequestLimits(LimitOverrides):
    @model_validator(mode="after")
    def paired_window(self) -> Self:
        if (self.window_requests is None) != (self.window_seconds is None):
            raise ValueError("window fields must be specified together")
        return self

    @property
    def is_unlimited(self) -> bool:
        return all(value is None for value in self.model_dump().values())


class TargetMatch(BudgetModel):
    platform_key: str | None = Field(default=None, min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] | None = None
    path_template: str | None = None
    mcp_server_key: str | None = Field(default=None, min_length=1)
    rpc_method: str | None = Field(default=None, min_length=1)
    tool_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def single_protocol(self) -> Self:
        http = self.platform_key is not None
        if http == (self.mcp_server_key is not None):
            raise ValueError("exactly one target protocol is required")
        if http and (self.rpc_method is not None or self.tool_name is not None):
            raise ValueError("HTTP target cannot carry MCP fields")
        if not http and (self.method is not None or self.path_template is not None):
            raise ValueError("MCP target cannot carry HTTP fields")
        if self.path_template is not None:
            self._validate_path()
        return self

    def _validate_path(self) -> None:
        path = self.path_template
        if path is None:
            return
        if not path.startswith("/") or "//" in path or any(c in path for c in ("?", "#", "\\", "\x00")):
            raise ValueError("invalid target path template")
        for part in path.split("/")[1:]:
            if "{" in part or "}" in part:
                if not (part.startswith("{") and part.endswith("}") and part[1:-1].isidentifier()):
                    raise ValueError("placeholder must occupy a whole path segment")
            elif part in (".", "..") or "*" in part or "%" in part:
                raise ValueError("ambiguous target path template")


class TargetRule(BudgetModel):
    id: str = Field(min_length=1)
    match: TargetMatch
    limits: RequestLimits


class SkillBudget(BudgetModel):
    total: RequestLimits = Field(default_factory=RequestLimits)
    targets: tuple[TargetRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        ids = [rule.id for rule in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate target rule id")
        return self

    @property
    def is_unlimited(self) -> bool:
        return self.total.is_unlimited and all(rule.limits.is_unlimited for rule in self.targets)


class SkillBudgetOverride(BudgetModel):
    total: LimitOverrides = Field(default_factory=LimitOverrides)
    targets: tuple[TargetRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        ids = [rule.id for rule in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate skill override target rule id")
        return self


class EgressBudgetConfig(BudgetModel):
    execution: RequestLimits = Field(default_factory=RequestLimits)
    skill_defaults: SkillBudget = Field(default_factory=SkillBudget)
    skills: dict[str, SkillBudgetOverride] = Field(default_factory=dict)
    targets: tuple[TargetRule, ...] = ()

    @model_validator(mode="after")
    def valid_names(self) -> Self:
        if any(not key.strip() for key in self.skills):
            raise ValueError("skill key must not be blank")
        ids = [rule.id for rule in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate execution target rule id")
        return self


class EffectiveEgressBudget(BudgetModel):
    execution: RequestLimits = Field(default_factory=RequestLimits)
    skill_defaults: SkillBudget = Field(default_factory=SkillBudget)
    skills: dict[str, SkillBudget] = Field(default_factory=dict)
    targets: tuple[TargetRule, ...] = ()

    def for_skill(self, key: str) -> SkillBudget:
        return self.skills.get(key, self.skill_defaults).model_copy(deep=True)

    @property
    def is_unlimited(self) -> bool:
        return (
            self.execution.is_unlimited
            and self.skill_defaults.is_unlimited
            and all(skill.is_unlimited for skill in self.skills.values())
            and all(rule.limits.is_unlimited for rule in self.targets)
        )


def _merge_skill(default: SkillBudget, override: SkillBudgetOverride) -> SkillBudget:
    values = default.total.model_dump()
    values.update(override.total.model_dump(exclude_unset=True))
    targets = {rule.id: rule for rule in default.targets}
    targets.update({rule.id: rule for rule in override.targets})
    return SkillBudget(total=RequestLimits.model_validate(values), targets=tuple(targets.values()))


def resolve_egress_budget(value: object) -> EffectiveEgressBudget:
    config = EgressBudgetConfig.model_validate(value)
    return EffectiveEgressBudget(
        execution=config.execution.model_copy(deep=True),
        skill_defaults=config.skill_defaults.model_copy(deep=True),
        skills={key: _merge_skill(config.skill_defaults, skill) for key, skill in config.skills.items()},
        targets=tuple(rule.model_copy(deep=True) for rule in config.targets),
    )
