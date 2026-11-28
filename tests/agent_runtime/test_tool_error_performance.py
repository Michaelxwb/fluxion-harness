"""Repeatable host-side workloads. Set TOOL_ERROR_SAMPLES to retain raw measurements."""
from __future__ import annotations

import json
import os
import resource
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_core.agent.failure import FailureSignal, update_failure_state
from muad_agent_core.agent.termination import FailureState
from muad_agent_runtime.application.run_service import RunService
from muad_agent_runtime.application.task_client import WorkerTaskClient
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_api import AppError
from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_view import build_report
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
)
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.engine.interfaces import DBAPICursor

from tests.console_platform.test_execution_export_business_fields import _bundle

CODE = "SCHEDULE_INVOCATION_CONTRACT_MISSING"
RUN = uuid.UUID(int=31)
TENANT = "performance-fixture"
WORKLOADS = ("success", "rejected", "recovery", "long-run", "export")


def _recovery() -> FailureState:
    signal = FailureSignal(tool="draft_schedule", tool_call_id="a", business_status="FAILED",
                           failure_layer="TOOL", error_code=CODE, attempt="draft:fixed")
    current = update_failure_state(FailureState(), signal, max_identical_failures=2)
    return FailureState.from_payload(current.as_payload())


async def _seed() -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.text("""
            INSERT INTO runtime.tool_call_audit
            (tenant_id, run_id, conversation_id, tool_call_id, tool_name, tool_kind,
             prepared_args_hash, status, business_status, failure_layer, error_code, start_time)
            SELECT :tenant, :run, :run, i::text, 'draft_schedule', 'BUILTIN',
                   'fixture', 'OK', CASE WHEN i % 4 = 0 THEN 'FAILED' ELSE 'SUCCEEDED' END,
                   CASE WHEN i % 4 = 0 THEN 'TOOL' ELSE 'NONE' END,
                   CASE WHEN i % 4 = 0 THEN :code END, now() FROM generate_series(1,256) i
        """), {"tenant": TENANT, "run": RUN, "code": CODE})
        await session.commit()


@pytest.mark.parametrize("workload", WORKLOADS)
async def test_fixed_workload(workload: str, isolated_datastore: object) -> None:
    assert isolated_datastore, "Requires the isolated PostgreSQL clone"
    counts: Counter[str] = Counter()
    bundle = _bundle(timeline_size=256)
    await _seed()

    def respond(request: httpx.Request) -> httpx.Response:
        counts["worker"] += 1
        if workload == "success":
            return httpx.Response(200, json={"data": {"status": "ok"}})
        return httpx.Response(422, json={"code": CODE, "msg": "该 Skill 缺少可调度的调用契约",
                                         "data": {"field": "payload.skill_id", "reason": "CONTRACT_MISSING",
                                                  "repair": "REIMPORT_SKILL"}})

    def sql(conn: Connection, cursor: DBAPICursor, statement: str,
            parameters: Sequence[object] | Mapping[str, object],
            context: ExecutionContext | None, executemany: bool) -> None:
        counts["sql"] += 1
        if statement.lstrip().upper().startswith("INSERT INTO RUNTIME.TOOL_CALL_AUDIT"):
            counts["audit_inserts"] += 1

    client = WorkerTaskClient("http://fixture", transport=httpx.MockTransport(respond))
    if workload == "long-run" and (target := os.environ.get("TOOL_ERROR_SAMPLES")):
        await _save_plan(Path(target).with_suffix(".plan.json"))
    engine = get_engine().sync_engine
    sa.event.listen(engine, "before_cursor_execute", sql)
    try:
        for iteration in range(int(os.environ.get("TOOL_ERROR_ITERATIONS", "3")) + 1):
            counts.clear()
            start, cpu = time.perf_counter_ns(), time.process_time_ns()
            await _operation(workload, client, bundle)
            elapsed, used = time.perf_counter_ns() - start, time.process_time_ns() - cpu
            assert counts["worker"] == int(workload in {"success", "rejected"})
            assert (counts["sql"] > 0) == (workload == "long-run")
            if iteration and (destination := os.environ.get("TOOL_ERROR_SAMPLES")):
                sample = dict(workload=workload, iteration=iteration, elapsed_ns=elapsed,
                              cpu_ns=used, rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              counts=dict(counts))
                with Path(destination).open("a", encoding="utf8") as stream:
                    stream.write(json.dumps(sample) + "\n")
    finally:
        sa.event.remove(engine, "before_cursor_execute", sql)
        await client.aclose()


async def _operation(workload: str, client: WorkerTaskClient, bundle: ExecutionExportBundle) -> None:
    if workload == "recovery":
        assert "draft:fixed" in _recovery().blocked_attempts
    elif workload == "export":
        assert build_report(bundle, build_diagnosis(bundle))["tool_calls"]
    elif workload == "long-run":
        async with get_session_factory()() as session:
            facts = await RunService._finalization_facts(
                object.__new__(RunService), session, tenant_id=TENANT, run_id=RUN,
                error_code="RUN_NO_PROGRESS", snapshot=None)
            assert facts.tool_outcomes.total == 256
    elif workload == "rejected":
        with pytest.raises(AppError) as rejected:
            await client._request("POST", "/internal/schedule-drafts", tenant_id=TENANT)
        assert rejected.value.code == CODE
    else:
        response = await client._request(
            "POST", "/internal/schedule-drafts", tenant_id=TENANT
        )
        assert response == {"status": "ok"}


async def _save_plan(path: Path) -> None:
    """Capture the SELECT actually issued by finalization, with its real bindings."""
    statements: list[tuple[str, object]] = []

    def capture(conn: Connection, cursor: DBAPICursor, statement: str,
                parameters: tuple[object, ...], context: ExecutionContext, many: bool) -> None:
        if "FROM runtime.tool_call_audit" in statement:
            statements.append((statement, parameters))

    engine = get_engine().sync_engine
    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        async with get_session_factory()() as session:
            await RunService._finalization_facts(object.__new__(RunService), session,
                tenant_id=TENANT, run_id=RUN, error_code="RUN_NO_PROGRESS", snapshot=None)
            assert len(statements) == 1
            statement, params = statements[0]
            connection = await session.connection()
            result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
            path.write_text(json.dumps(result.scalar_one()), encoding="utf8")
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)
