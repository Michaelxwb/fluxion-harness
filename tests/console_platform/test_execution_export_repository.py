"""Execution-export repository: full chain, task tree, snapshot and section degradation.

Covers B-01 / B-02 / B-03 / E-04 against real PostgreSQL. The repository must own the
first statement of the request so the repeatable-read snapshot is established before
any section is read.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportRepository,
    ExportSection,
)

from console_platform.conftest import TenantContext
from tests.console_platform.execution_export_fixtures import (
    _run,
    _seed_event,
    _seed_task,
    cleanup_execution_export_rows,  # noqa: F401
    repo_session,
    seed_child_task,
    seed_task_event,
)

pytestmark = pytest.mark.usefixtures("cleanup_execution_export_rows")


@pytest.mark.asyncio
async def test_b01_exports_all_non_delta_events(tenant: TenantContext) -> None:
    """B-01: the full non-delta chain is exported, independent of the UI 200 cap."""
    seed = await _run(tenant, status="FAILED", error_code="RUN_NO_PROGRESS")
    async with repo_session()() as seed_session:
        async with seed_session.begin():
            for seq in range(1, 251):
                await _seed_event(seed_session, tenant, seed, seq=seq, event_type="TOOL_CALL")
            await _seed_event(seed_session, tenant, seed, seq=251, event_type="ASSISTANT_DELTA")

    async with repo_session()() as session:
        bundle = await ExecutionExportRepository(session).run_bundle(tenant.tenant_id, seed.run_id)

    assert bundle is not None
    events = bundle.sections["runtime.canonical_event"]
    assert events.status == "ok"
    assert len(events.rows) == 250
    assert all(row["event_type"] != "ASSISTANT_DELTA" for row in events.rows)
    assert bundle.truncation["truncated"] is False


@pytest.mark.asyncio
async def test_b02_batch_children_are_collected(tenant: TenantContext) -> None:
    """B-02: the recursive task tree is collected; failed child events are included."""
    seed = await _run(tenant, status="FAILED", error_code="RUN_NO_PROGRESS")
    async with repo_session()() as seed_session:
        async with seed_session.begin():
            parent_id = await _seed_task(seed_session, tenant, seed, status="FAILED")
            failed_child = await seed_child_task(
                seed_session, tenant, seed, parent_id=parent_id, status="FAILED"
            )
            ok_child = await seed_child_task(
                seed_session, tenant, seed, parent_id=parent_id, status="COMPLETED"
            )
            await seed_task_event(
                seed_session,
                tenant,
                task_id=failed_child,
                trace_id=f"trace-{uuid.uuid4().hex}",
            )

    async with repo_session()() as session:
        bundle = await ExecutionExportRepository(session).run_bundle(tenant.tenant_id, seed.run_id)

    assert bundle is not None
    tasks = bundle.sections["task.task_execution"]
    assert tasks.status == "ok", tasks.error_message
    task_ids = {row["id"] for row in tasks.rows}
    assert str(parent_id) in task_ids or parent_id in task_ids
    assert str(failed_child) in task_ids or failed_child in task_ids
    assert str(ok_child) in task_ids or ok_child in task_ids

    events = bundle.sections["task.task_event"]
    assert events.status == "ok"
    assert any(
        (row.get("task_id") == str(failed_child) or row.get("task_id") == failed_child) for row in events.rows
    )


@pytest.mark.asyncio
async def test_b03_reports_snapshot_metadata(tenant: TenantContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """B-03: all sections read one read-only repeatable-read snapshot."""
    seed = await _run(tenant, status="COMPLETED")
    async with repo_session()() as seed_session:
        async with seed_session.begin():
            await _seed_event(seed_session, tenant, seed, seq=1, event_type="RUN_CREATED")

    async with repo_session()() as session:
        repository = ExecutionExportRepository(session)
        original = repository._read

        async def read_with_concurrent_insert(
            name: str,
            sql: str,
            params: dict[str, Any],
            *,
            required: bool = False,
            row_limit: int | None = None,
        ) -> ExportSection:
            # Insert after the event section has been read but before the next section:
            # the read-only snapshot must not see this later row.
            if name == "runtime.tool_call_audit":
                async with repo_session()() as writer:
                    async with writer.begin():
                        await _seed_event(writer, tenant, seed, seq=2, event_type="RUN_FAILED")
            return await original(name, sql, params, required=required, row_limit=row_limit)

        monkeypatch.setattr(repository, "_read", read_with_concurrent_insert)
        bundle = await repository.run_bundle(tenant.tenant_id, seed.run_id)

    assert bundle is not None
    events = bundle.sections["runtime.canonical_event"]
    assert events.status == "ok"
    assert [row["seq"] for row in events.rows] == [1]
    snapshot = bundle.database_snapshot
    assert snapshot["transaction_isolation"] == "repeatable read"
    assert snapshot["transaction_read_only"] == "on"
    assert snapshot["snapshot_time"]


@pytest.mark.asyncio
async def test_e04_optional_section_failure_is_visible_not_silent(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-04: an optional section may fail without losing the core execution facts."""
    seed = await _run(tenant, status="FAILED", error_code="RUN_NO_PROGRESS")
    async with repo_session()() as seed_session:
        async with seed_session.begin():
            await _seed_event(seed_session, tenant, seed, seq=1, event_type="RUN_CREATED")

    async with repo_session()() as session:
        repository = ExecutionExportRepository(session)
        original = repository._read

        async def flaky_read(
            name: str,
            sql: str,
            params: dict[str, Any],
            *,
            required: bool = False,
            row_limit: int | None = None,
        ) -> ExportSection:
            if name == "runtime.run_continuation":
                return ExportSection(status="error", error_code="InjectedFailure")
            return await original(name, sql, params, required=required, row_limit=row_limit)

        monkeypatch.setattr(repository, "_read", flaky_read)
        bundle = await repository.run_bundle(tenant.tenant_id, seed.run_id)

    assert bundle is not None
    assert bundle.sections["runtime.run_continuation"].status == "error"
    assert bundle.sections["runtime.canonical_event"].status == "ok"
    assert bundle.record["id"] == str(seed.run_id) or bundle.record["id"] == seed.run_id
    assert any(item["section"] == "runtime.run_continuation" for item in bundle.warnings)
