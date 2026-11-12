"""Real PG audit writes feed the same SQL projection used by Console Overview."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_runtime.infrastructure.models.runtime import EgressAudit
from muad_console_platform.infrastructure.repositories.overview_sql import OVERVIEW_SQL
from muad_platform_sdk.egress_attempt_audit import AttemptAudit, EgressAttempt
from muad_platform_sdk.egress_audit_store import DatabaseAttemptObserver, EgressAuditIdentity
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration


async def test_terminal_audits_are_idempotent_tenant_scoped_and_visible_to_overview(async_tool_database):
    sessions = async_sessionmaker(async_tool_database, expire_on_commit=False)
    identity = EgressAuditIdentity("audit-" + uuid.uuid4().hex, uuid.uuid4(), uuid.uuid4())
    observer = DatabaseAttemptObserver(sessions, identity, timeout_seconds=2)
    success = EgressAttempt(uuid.uuid4().hex, "https://platform.test", "GET /assets", "GET",
                            True, "COMPLETED", 200, 10, None)
    failure = replace(success, attempt_id=uuid.uuid4().hex, result_status="FAILED", status_code=503)
    blocked = replace(success, attempt_id=uuid.uuid4().hex, sent=False, result_status="BLOCKED",
                      status_code=None, error_code="LIMIT_WINDOW")
    for event in (success, success, failure, blocked):
        await observer(event)
    other = DatabaseAttemptObserver(sessions, replace(identity, tenant_id="other-" + identity.tenant_id),
                                    timeout_seconds=2)
    await other(success)
    async with sessions.begin() as session:
        for kind, phase in (("MCP_TOOL", "completed"), ("HTTP", "started")):
            session.add(EgressAudit(
                tenant_id=identity.tenant_id, run_id=identity.run_id, user_id=identity.user_id,
                target_type=kind, target="https://platform.test", operation="logical-or-phase",
                policy_decision="ALLOW", result_status="OK", phase=phase,
            ))
    async with sessions() as session:
        rows = (await session.scalars(select(EgressAudit).where(
            EgressAudit.tenant_id == identity.tenant_id,
        ))).all()
        now = datetime.now(UTC)
        overview = await session.scalar(text(OVERVIEW_SQL), {
            "tenant_id": identity.tenant_id, "now": now, "start": now - timedelta(days=1), "tz": "UTC",
        })
    assert len(rows) == 5 and {row.run_id for row in rows} == {identity.run_id}
    assert overview["egress"]["request_count"] == 3
    assert overview["egress"]["sent_count"] == 2
    assert overview["egress"]["completed_count"] == 1
    assert overview["egress"]["failed_count"] == 1
    assert overview["egress"]["blocked_count"] == 1


async def test_standalone_task_audit_keeps_real_task_without_fake_run(async_tool_database):
    sessions = async_sessionmaker(async_tool_database, expire_on_commit=False)
    identity = EgressAuditIdentity("task-audit-" + uuid.uuid4().hex, uuid.uuid4(), None, uuid.uuid4())
    route = "GET /" + "long-interface-segment/" * 24
    await DatabaseAttemptObserver(sessions, identity, timeout_seconds=2)(
        EgressAttempt(uuid.uuid4().hex, "https://platform.test", route, "GET",
                      True, "COMPLETED", 200, 2, None)
    )
    async with sessions() as session:
        row = (await session.scalars(select(EgressAudit).where(
            EgressAudit.tenant_id == identity.tenant_id,
        ))).one()
    assert row.run_id is None and row.task_id == identity.task_id and row.user_id == identity.user_id
    assert len(row.operation) > 256 and row.operation == route


async def test_failed_observer_logs_once_without_rewriting_attempt(caplog):
    calls = []

    async def failed(event):
        calls.append(event)
        raise RuntimeError("observer unavailable")

    audit = AttemptAudit(failed, origin="https://platform.test", path="/assets", method="GET")
    audit.sent, audit.status_code = True, 200
    await audit.finish()
    await audit.finish()
    assert len(calls) == 1 and calls[0].result_status == "COMPLETED"
    assert "egress_attempt_audit_failed" in caplog.text


async def test_attempt_audit_keeps_the_actual_request_path():
    events = []

    async def observe(event):
        events.append(event)

    audit = AttemptAudit(observe, origin="https://platform.test", path="/api/v1/targets", method="GET")
    await audit.finish()
    assert len(events) == 1 and events[0].route_id == "GET /api/v1/targets"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def test_overview_separates_platform_method_and_url(async_tool_database, method):
    from muad_console_platform.infrastructure.models.control import ProjectPlatform

    sessions = async_sessionmaker(async_tool_database, expire_on_commit=False)
    identity = EgressAuditIdentity("columns-" + uuid.uuid4().hex, uuid.uuid4(), uuid.uuid4())
    async with sessions.begin() as session:
        platforms = ((identity.tenant_id, "Platform A"), ("other-" + identity.tenant_id, "Wrong tenant"))
        for tenant, name in platforms:
            session.add(ProjectPlatform(
                id=uuid.uuid4(), tenant_id=tenant, key="test", name=name, resolver_type="STATIC",
                resolver_config_json={"base_url": "https://platform.test"},
                adapter_key="generic-http", credential_mode="NONE",
            ))
    await DatabaseAttemptObserver(sessions, identity, timeout_seconds=2)(
        EgressAttempt(uuid.uuid4().hex, "https://platform.test", method + " /api/v1/targets", method,
                      True, "COMPLETED", 200, 2, None)
    )
    async with sessions() as session:
        now = datetime.now(UTC)
        overview = await session.scalar(text(OVERVIEW_SQL), {
            "tenant_id": identity.tenant_id, "now": now, "start": now - timedelta(days=1), "tz": "UTC",
        })
    assert overview["egress"]["top_targets"] == [{
        "url": "https://platform.test/api/v1/targets", "method": method, "platform_name": "Platform A",
        "request_count": 1, "blocked_count": 0,
    }]
