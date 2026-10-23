"""Overview: authenticated HTTP -> real PostgreSQL, no mocked aggregate or storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.application.audit_query_service import format_console_time
from muad_console_platform.infrastructure.repositories.overview_models import OverviewPayload
from muad_console_platform.main import app

from console_platform.conftest import TenantContext
from console_platform.overview_fixtures import (
    NOW,
    _seed_agent,
    _seed_platform_user,
    _seed_schedule,
    _seed_skill,
    seed_model,
    seed_run,
    seed_task,
    seed_timezone,
)
from console_platform.overview_fixtures import overview_tenant as overview_tenant

ENVELOPE_KEYS = {"code", "msg", "data", "trace_id", "request_id", "timestamp"}


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("muad_console_platform.application.overview_query_service.overview_now", lambda: NOW)


async def _overview(client: AsyncClient, days: int = 7) -> OverviewPayload:
    response = await client.get("/api/v1/overview", params={"days": days})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ENVELOPE_KEYS and body["code"] == "0"
    return OverviewPayload.model_validate(body["data"])


async def test_empty_data_retains_unknown_rates_and_real_zero_counts(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    data = await _overview(client)
    assert data.resources.enabled_models == 1  # account fixture has one enabled and one disabled model
    assert data.kpis.run_count == data.kpis.active_users == data.kpis.model_request_count == 0
    assert data.kpis.run_success_rate is None and data.kpis.average_run_duration_ms is None
    assert data.kpis.total_tokens == 0 and data.kpis.token_coverage is None
    assert data.agent_ranking == [] and data.status.runs == data.status.tasks == {}
    assert not any(data.activity.model_dump().values())
    assert len(data.trend.runs) == len(data.trend.tasks) == len(data.trend.tokens) == 7
    assert all(point.total == point.failed == 0 for point in data.trend.runs)
    assert data.timezone == "Asia/Shanghai" and data.generated_at == "2026-10-11 18:00:00+08:00"


async def test_resource_counts_are_enabled_live_and_tenant_scoped(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    t = overview_tenant
    agent = await _seed_agent(t, name="Live")
    await _seed_agent(t, name="Disabled", enabled=False)
    await _seed_agent(t, name="Deleted", is_deleted=True)
    other = TenantContext(t.tenant_id + "-other", t.model_id, t.disabled_model_id)
    await _seed_agent(other, name="Other tenant")
    await _seed_skill(t)
    await _seed_skill(t, enabled=False)
    user = await _seed_platform_user(t, display_name="Actor")
    await _seed_schedule(t, agent_id=agent, actor_user_id=user, name="Active")
    await _seed_schedule(t, agent_id=agent, actor_user_id=user, name="Paused", status="PAUSED")
    data = await _overview(client)
    assert data.resources.model_dump() == {
        "enabled_agents": 1,
        "enabled_skills": 1,
        "enabled_mcps": 0,
        "enabled_models": 1,
        "users": 1,
        "active_schedules": 1,
    }


@pytest.mark.parametrize("status", ["CREATED", "RUNNING", "WAITING_INPUT", "WAITING_TOOL", "CANCELLED"])
async def test_unfinished_and_cancelled_runs_do_not_enter_success_denominator(
    client: AsyncClient, overview_tenant: TenantContext, status: str
) -> None:
    await seed_run(overview_tenant, status=status)
    data = await _overview(client)
    assert data.kpis.run_count == 1 and data.kpis.run_success_rate is None
    assert data.status.runs == {status: 1}


async def test_success_rate_duration_and_distinct_user_use_runs_not_audits(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    user = uuid.uuid4()
    for status in ("COMPLETED", "FAILED", "CANCELLED"):
        run = await seed_run(
            overview_tenant, status=status, user=user, start=NOW - timedelta(seconds=10), end=NOW
        )
        for attempt in range(3):
            await seed_model(overview_tenant, run, inputs=10, outputs=5, attempt=attempt)
    await seed_run(overview_tenant, status="RUNNING", user=user)
    data = await _overview(client)
    assert data.kpis.run_count == 4 and data.kpis.active_users == 1
    assert data.kpis.run_success_rate == 50 and data.kpis.average_run_duration_ms == 10000
    assert data.kpis.model_request_count == 9 and data.kpis.total_tokens == 135
    assert data.attention.failed_runs == 1


@pytest.mark.parametrize(
    "inputs,outputs,total,reported",
    [(None, None, None, 0), (0, 0, 0, 1), (0, None, 0, 0), (None, 4, 4, 0), (10, 3, 13, 1)],
)
async def test_token_unknown_zero_and_partial_reporting(
    client: AsyncClient,
    overview_tenant: TenantContext,
    inputs: int | None,
    outputs: int | None,
    total: int | None,
    reported: int,
) -> None:
    run = await seed_run(overview_tenant)
    await seed_model(overview_tenant, run, inputs=inputs, outputs=outputs)
    data = await _overview(client)
    assert (data.kpis.input_tokens, data.kpis.output_tokens, data.kpis.total_tokens) == (
        inputs,
        outputs,
        total,
    )
    assert data.kpis.token_reported_request_count == reported and data.kpis.token_coverage == reported * 100
    assert data.trend.tokens[-1].input_tokens == inputs and data.trend.tokens[-1].output_tokens == outputs
    assert data.trend.tokens[0].input_tokens == data.trend.tokens[0].output_tokens == 0


async def test_retries_summaries_and_partial_usage_are_all_recorded(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    run = await seed_run(overview_tenant)
    await seed_model(overview_tenant, run, status="RETRY", inputs=10)
    await seed_model(overview_tenant, run, status="ERROR")
    await seed_model(overview_tenant, run, inputs=20, outputs=5, attempt=1)
    await seed_model(overview_tenant, run, inputs=2, outputs=1, model="summary")
    data = await _overview(client)
    assert data.kpis.model_request_count == 4
    assert (data.kpis.input_tokens, data.kpis.output_tokens, data.kpis.total_tokens) == (32, 6, 38)
    assert data.kpis.token_reported_request_count == 2 and data.kpis.token_coverage == 50
    assert data.kpis.run_count == 1


async def test_foreign_deleted_future_and_orphan_audits_do_not_leak(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    t = overview_tenant
    other = TenantContext(t.tenant_id + "-other", t.model_id, t.disabled_model_id)
    live = await seed_run(t)
    deleted = await seed_run(t, deleted=True)
    foreign = await seed_run(other)
    future = await seed_run(t, created=NOW + timedelta(seconds=1))
    for run in (deleted, foreign, future, uuid.uuid4()):
        await seed_model(t, run, inputs=999)
    await seed_model(other, live, inputs=999)
    await seed_model(t, live, inputs=999, deleted=True)
    await seed_model(t, live, inputs=999, created=NOW + timedelta(seconds=1))
    await seed_model(t, live, inputs=999, created=NOW - timedelta(days=31))
    await seed_model(t, live, inputs=10, outputs=0)
    data = await _overview(client)
    assert data.kpis.run_count == data.kpis.model_request_count == data.kpis.active_users == 1
    assert data.kpis.total_tokens == 10


@pytest.mark.parametrize("days,expected", [(1, 1), (7, 2), (30, 3)])
async def test_calendar_window_and_state_distribution_use_selected_period(
    client: AsyncClient, overview_tenant: TenantContext, days: int, expected: int
) -> None:
    for age in (0, 2, 20, 31):
        await seed_run(overview_tenant, created=NOW - timedelta(days=age), status="COMPLETED")
        await seed_task(overview_tenant, created=NOW - timedelta(days=age), status="COMPLETED")
    data = await _overview(client, days)
    assert data.kpis.run_count == expected and sum(data.status.runs.values()) == expected
    assert sum(data.status.tasks.values()) == expected
    assert len(data.trend.runs) == days and sum(point.total for point in data.trend.runs) == expected


@pytest.mark.parametrize("timezone", ["Asia/Shanghai", "America/New_York"])
async def test_local_midnight_and_platform_timestamp(
    client: AsyncClient, overview_tenant: TenantContext, timezone: str
) -> None:
    from zoneinfo import ZoneInfo

    await seed_timezone(overview_tenant, timezone)
    midnight = NOW.astimezone(ZoneInfo(timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    await seed_run(overview_tenant, created=midnight - timedelta(microseconds=1))
    await seed_run(overview_tenant, created=midnight)
    data = await _overview(client, 1)
    assert data.kpis.run_count == 1 and data.trend.runs[0].total == 1
    assert data.timezone == timezone
    assert data.generated_at == format_console_time(NOW, timezone=ZoneInfo(timezone))


async def test_failure_trend_uses_finish_date_including_older_created_work(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    await seed_run(overview_tenant, created=NOW - timedelta(days=20), status="FAILED", end=NOW)
    await seed_task(overview_tenant, created=NOW - timedelta(days=20), status="FAILED", finished=NOW)
    data = await _overview(client, 1)
    assert data.kpis.run_count == data.attention.failed_runs == data.attention.failed_tasks == 0
    assert data.trend.runs[0].total == data.trend.tasks[0].total == 0
    assert data.trend.runs[0].failed == data.trend.tasks[0].failed == 1


async def test_task_root_scope_snapshot_and_delivery_failures(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    t = overview_tenant
    parent = await seed_task(t, status="RUNNING", created=NOW - timedelta(days=20), deadline=NOW)
    await seed_task(t, status="FAILED", parent=parent, finished=NOW, delivery="FAILED")
    await seed_task(t, status="FAILED", finished=NOW, delivery="FAILED")
    await seed_task(t, status="FAILED", deleted=True)
    await seed_task(t, created=NOW + timedelta(seconds=1))
    await seed_task(t, status="COMPLETED", delivery="FAILED", mode="NONE")
    data = await _overview(client, 1)
    assert data.activity.running_tasks == data.activity.overdue_tasks == 1
    assert data.activity.queued_tasks == 0
    assert data.attention.failed_tasks == data.attention.failed_deliveries == 1
    assert data.status.tasks == {"FAILED": 1, "COMPLETED": 1}
    assert data.trend.tasks[-1].total == 2 and data.trend.tasks[-1].failed == 1


async def test_current_run_waiting_counts_are_independent_of_period(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    for status in ("CREATED", "RUNNING", "WAITING_INPUT", "WAITING_TOOL"):
        await seed_run(overview_tenant, status=status, created=NOW - timedelta(days=40))
    data = await _overview(client, 1)
    assert data.kpis.run_count == 0 and data.activity.active_runs == 4
    assert data.activity.waiting_input_runs == data.activity.waiting_tool_runs == 1


async def test_ranking_ties_are_stable_and_deleted_agent_history_retained(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    ids = []
    for index in range(7):
        agent = await _seed_agent(overview_tenant, name="Agent " + str(index), is_deleted=index == 0)
        ids.append(agent)
        run = await seed_run(overview_tenant, agent=agent)
        await seed_model(overview_tenant, run, inputs=10, outputs=1)
        await seed_model(overview_tenant, run, inputs=20, outputs=2)
    data = await _overview(client)
    assert len(data.agent_ranking) == 5
    assert [row.agent_id for row in data.agent_ranking] == sorted(ids)[:5]
    assert all(row.run_count == 1 for row in data.agent_ranking) and data.kpis.run_count == 7
    if ids[0] in sorted(ids)[:5]:
        assert next(row for row in data.agent_ranking if row.agent_id == ids[0]).agent_name is None


@pytest.mark.parametrize(
    "start,end", [(None, NOW), (NOW, NOW - timedelta(seconds=1)), (NOW, NOW + timedelta(seconds=1))]
)
async def test_invalid_and_future_durations_remain_unknown(
    client: AsyncClient, overview_tenant: TenantContext, start: datetime | None, end: datetime
) -> None:
    await seed_run(overview_tenant, start=start, end=end)
    assert (await _overview(client)).kpis.average_run_duration_ms is None


@pytest.mark.parametrize("days", [0, 31, "invalid"])
async def test_days_validation_uses_standard_error_envelope(client: AsyncClient, days: int | str) -> None:
    response = await client.get("/api/v1/overview", params={"days": days})
    assert response.status_code == 422
    assert set(response.json()) == ENVELOPE_KEYS and response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_authenticated_account_tenant_cannot_be_overridden(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    await seed_run(overview_tenant)
    response = await client.get("/api/v1/overview", headers={"X-Tenant-Id": "other"})
    assert response.status_code == 200 and response.json()["data"]["kpis"]["run_count"] == 1


async def test_rule_api_001_envelope_and_unauthenticated_error() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/overview")
    assert response.status_code == 401
    assert set(response.json()) == ENVELOPE_KEYS and response.json()["code"] == "UNAUTHORIZED"


async def test_removed_metrics_route_has_no_compatibility_layer(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/overview/metrics")).status_code == 404


def test_b201_docs_07_registers_current_overview_contract() -> None:
    text = (Path(__file__).resolve().parents[2] / "docs/07-跨模块接口与协议详细设计.md").read_text()
    section = text.split("### 10.12 Overview", 1)[1].split("\n## ", 1)[0]
    for key in (
        "GET /api/v1/overview",
        "days",
        "resources",
        "activity",
        "attention",
        "agent_ranking",
        "token_coverage",
        "tenant_id",
    ):
        assert key in section, key


@pytest.mark.parametrize(
    "zone,expected",
    [
        ("Asia/Shanghai", "2026-10-11 18:00:00+08:00"),
        ("America/New_York", "2026-10-11 06:00:00-04:00"),
    ],
)
def test_console_time_formatter_uses_explicit_display_timezone(zone: str, expected: str) -> None:
    assert format_console_time(NOW, timezone=ZoneInfo(zone)) == expected
    assert format_console_time(NOW.replace(tzinfo=None), timezone=ZoneInfo(zone)) == expected
    assert format_console_time(None, timezone=ZoneInfo(zone)) is None


def test_console_time_formatter_default_uses_utc_offset() -> None:
    value = datetime(2026, 10, 11, 10, tzinfo=UTC)
    assert format_console_time(value) == "2026-10-11 10:00:00+00:00"
