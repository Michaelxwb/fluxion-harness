"""Typed read models for the tenant-scoped Overview aggregate."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class OverviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverviewResources(OverviewModel):
    enabled_agents: int
    enabled_skills: int
    enabled_mcps: int
    enabled_models: int
    users: int
    active_schedules: int


class OverviewKpis(OverviewModel):
    run_count: int
    run_success_rate: float | None
    average_run_duration_ms: float | None
    active_users: int
    model_request_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    token_reported_request_count: int
    token_coverage: float | None


class OverviewActivity(OverviewModel):
    active_runs: int
    queued_tasks: int
    running_tasks: int
    waiting_tasks: int
    waiting_input_runs: int
    waiting_tool_runs: int
    overdue_tasks: int


class OverviewAttention(OverviewModel):
    failed_runs: int
    failed_tasks: int
    failed_deliveries: int


class OverviewTrendPoint(OverviewModel):
    date: str
    total: int
    failed: int


class OverviewTokenPoint(OverviewModel):
    date: str
    input_tokens: int | None
    output_tokens: int | None
    model_request_count: int
    token_reported_request_count: int


class OverviewTrend(OverviewModel):
    runs: list[OverviewTrendPoint]
    tasks: list[OverviewTrendPoint]
    tokens: list[OverviewTokenPoint]


class OverviewStatus(OverviewModel):
    runs: dict[str, int]
    tasks: dict[str, int]


class OverviewAgentRanking(OverviewModel):
    agent_id: uuid.UUID
    agent_name: str | None
    run_count: int


class OverviewEgressTop(OverviewModel):
    url: str
    method: str | None
    platform_name: str | None
    request_count: int
    blocked_count: int


class OverviewEgress(OverviewModel):
    request_count: int
    sent_count: int
    completed_count: int
    failed_count: int
    blocked_count: int
    average_latency_ms: float | None
    top_targets: list[OverviewEgressTop]


class OverviewMetrics(OverviewModel):
    resources: OverviewResources
    kpis: OverviewKpis
    activity: OverviewActivity
    attention: OverviewAttention
    trend: OverviewTrend
    status: OverviewStatus
    agent_ranking: list[OverviewAgentRanking]
    egress: OverviewEgress


class OverviewPayload(OverviewMetrics):
    days: int
    timezone: str
    generated_at: str
