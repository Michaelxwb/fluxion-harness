"""Console 经验管理读契约：双视图共用同一投影。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime

from .tasks import ContractModel

ActivationState = Literal["PROPOSED", "TRIAL", "ACTIVE", "RETIRED"]
Authority = Literal["USER_EXPLICIT", "ADMIN_EDIT", "AGENT_INFERRED"]
ReportOwner = Literal["OWNED", "UNKNOWN", "ABSENT"]


class ExperienceReport(ContractModel):
    version: int
    status: str
    improvement: float | None = None
    experience_user_id: str | None = None
    owner: ReportOwner
    base_config_hash: str
    metrics: dict[str, Any]
    decision: dict[str, Any]
    report: dict[str, Any]
    create_time: AwareDatetime


class ExperienceItem(ContractModel):
    user_id: str
    memory_key: str
    version: int
    category: str
    activation_state: ActivationState
    authority: Authority
    task_signature: str | None = None
    trigger_text: str | None = None
    content: dict[str, Any]
    author: str
    enabled: bool
    expires_at: AwareDatetime | None = None
    update_time: AwareDatetime
    latest_report: ExperienceReport | None = None
    report_owner: ReportOwner = "ABSENT"


class ExperienceCounts(ContractModel):
    PROPOSED: int = 0
    TRIAL: int = 0
    ACTIVE: int = 0
    RETIRED: int = 0
    total: int = 0


class ExperienceList(ContractModel):
    items: list[ExperienceItem]
    counts: ExperienceCounts


class ExperienceInjection(ContractModel):
    run_id: str
    score: float | None = None
    channel: str | None = None
    selection_hash: str | None = None
    create_time: AwareDatetime


class ExperienceDetail(ExperienceItem):
    evidence: dict[str, Any]
    history: list[dict[str, Any]]
    reports: list[ExperienceReport]
    injections: list[ExperienceInjection]
    audits: list[dict[str, Any]]


class FamilyItem(ContractModel):
    family: str
    source: Literal["skill", "agent", "unknown"]
    run_count: int
    run_count_capped: bool
    last_run_at: AwareDatetime | None = None
    experience_count: int
    states: ExperienceCounts
    latest_report_at: AwareDatetime | None = None
    pass_rate: dict[str, int]
    last_decision: str | None = None


class FamilyRun(ContractModel):
    id: str
    status: str
    user_id: str | None = None
    agent_id: str
    end_time: AwareDatetime | None = None


class FamilyCase(ContractModel):
    id: str
    source_run_id: str
    item_count: int
    version: int
    create_time: AwareDatetime


class FamilyDetail(ContractModel):
    family: str
    source: Literal["skill", "agent", "unknown"]
    window: str
    run_count: int
    run_count_capped: bool
    recent_runs: list[FamilyRun]
    cases: list[FamilyCase]
    experiences: list[dict[str, Any]]
    reports: list[ExperienceReport]
    paused: bool = False


class LearningActionRequest(ContractModel):
    reason: str | None = None


class LearningJobItem(ContractModel):
    id: str
    status: str
    job_type: str
    user_id: str
    experience_key: str
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    create_time: AwareDatetime
    update_time: AwareDatetime


class LearningActionItem(ContractModel):
    experience_key: str
    activation_state: str
    status: str
