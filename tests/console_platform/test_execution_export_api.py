"""TASK-004: Run/Task execution-chain export HTTP contract."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.main import app
from sqlalchemy import text

from console_platform.conftest import (
    ADMIN_PASSWORD,
    CSRF_COOKIE,
    CSRF_HEADER,
    TenantContext,
)
from tests.console_platform.execution_export_fixtures import (
    _run,
    _seed_task,
    cleanup_execution_export_rows,  # noqa: F401
    repo_session,
    seed_event_payload,
    seed_task_event,
)

pytestmark = pytest.mark.usefixtures("cleanup_execution_export_rows")


async def _builder_client(tenant: TenantContext) -> AsyncClient:
    username = f"builder-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        account = ConsoleAccount(
            tenant_id=tenant.tenant_id,
            username=username,
            display_name="Console Builder",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_BUILDER,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": ADMIN_PASSWORD},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER] = client.cookies.get(CSRF_COOKIE) or ""
    client._builder_account_id = account_id  # type: ignore[attr-defined]
    return client


async def _close_builder_client(client: AsyncClient) -> None:
    account_id = getattr(client, "_builder_account_id", None)
    await client.aclose()
    if account_id is None:
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM control.console_session WHERE account_id = :account_id"),
            {"account_id": account_id},
        )
        await session.execute(
            text("DELETE FROM control.console_account WHERE id = :account_id"),
            {"account_id": account_id},
        )
        await session.commit()


async def _seed_failed_run(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    seed = await _run(tenant, status="FAILED", error_code="RUN_NO_PROGRESS")
    async with repo_session()() as session:
        async with session.begin():
            task_id = await _seed_task(session, tenant, seed, status="FAILED")
            await seed_task_event(session, tenant, task_id=task_id)
            await seed_event_payload(
                session,
                tenant,
                seed,
                seq=1,
                event_type="RUN_CREATED",
                payload='{"status": "CREATED"}',
            )
            await seed_event_payload(
                session,
                tenant,
                seed,
                seq=2,
                event_type="TOOL_CALL",
                payload=json.dumps(
                    {
                        "status": "ERROR",
                        "error_code": "SKILL_EXECUTION_FAILED",
                        "tool_call_id": "call-1",
                        "task_id": str(task_id),
                        "text": "工具失败诊断-原文",
                    },
                    ensure_ascii=False,
                ),
            )
    return seed.run_id, task_id


@pytest.mark.asyncio
async def test_s01_failed_run_export_zip(client: AsyncClient, tenant: TenantContext) -> None:
    run_id, task_id = await _seed_failed_run(tenant)

    response = await client.get(f"/api/v1/runs/{run_id}/export?format=zip")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-export-complete"] == "true"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert tuple(archive.namelist()) == (
            "report.json",
            "report.md",
            "events.csv",
            "failures.csv",
            "calls.csv",
            "tasks.csv",
            "README.md",
        )
        report = json.loads(archive.read("report.json"))
    assert report["execution"]["kind"] == "RUN"
    assert report["execution"]["id"] == str(run_id)
    assert report["execution"]["outcome"] == "FAILED"
    assert report["diagnosis"]["primary_failure"] is not None
    assert report["diagnosis"]["primary_failure"]["service"] == "muad-agent-worker"
    assert any(task["task_id"] == str(task_id) for task in report["tasks"])


@pytest.mark.asyncio
async def test_s02_failed_task_export_zip(client: AsyncClient, tenant: TenantContext) -> None:
    run_id, task_id = await _seed_failed_run(tenant)

    response = await client.get(f"/api/v1/tasks/{task_id}/export?format=zip")

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read("report.json"))
    assert report["execution"]["kind"] == "TASK"
    assert report["execution"]["task_id"] == str(task_id)
    assert report["execution"]["source_run_id"] == str(run_id)
    assert report["execution"]["status"] == "FAILED"
    assert report["diagnosis"]["primary_failure"]["service"] == "muad-agent-worker"


@pytest.mark.asyncio
async def test_s03_successful_run_export_json(client: AsyncClient, tenant: TenantContext) -> None:
    seed = await _run(tenant, status="COMPLETED")
    async with repo_session()() as session:
        async with session.begin():
            await seed_event_payload(
                session,
                tenant,
                seed,
                seq=1,
                event_type="RUN_CREATED",
                payload='{"status": "CREATED"}',
            )

    response = await client.get(f"/api/v1/runs/{seed.run_id}/export?format=json")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    report = response.json()
    assert report["execution"]["outcome"] == "SUCCEEDED"
    assert report["diagnosis"]["primary_failure"] is None
    assert report["data_policy"]["diagnostic_fields"] == "raw"


@pytest.mark.asyncio
async def test_e01_cross_tenant_run_export_is_not_found(client: AsyncClient, tenant: TenantContext) -> None:
    foreign = await _run(tenant, tenant_id=f"{tenant.tenant_id}-other", status="FAILED")

    response = await client.get(f"/api/v1/runs/{foreign.run_id}/export?format=json")

    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"
    assert str(foreign.run_id) not in response.text


@pytest.mark.asyncio
async def test_export_writes_audit_log_without_content(
    client: AsyncClient, tenant: TenantContext, caplog: pytest.LogCaptureFixture
) -> None:
    run_id, _task_id = await _seed_failed_run(tenant)
    with caplog.at_level(logging.INFO):
        response = await client.get(f"/api/v1/runs/{run_id}/export?format=json")

    assert response.status_code == 200
    assert any(record.message == "execution_export" for record in caplog.records)
    assert "工具失败诊断-原文" not in caplog.text
    assert "sk-" not in caplog.text


async def test_e05_builder_cannot_export(client: AsyncClient, tenant: TenantContext) -> None:
    run_id, task_id = await _seed_failed_run(tenant)
    builder = await _builder_client(tenant)
    try:
        run_response = await builder.get(f"/api/v1/runs/{run_id}/export?format=json")
        task_response = await builder.get(f"/api/v1/tasks/{task_id}/export?format=json")
    finally:
        await _close_builder_client(builder)

    assert run_response.status_code == 403
    assert run_response.json()["code"] == "FORBIDDEN"
    assert task_response.status_code == 403
    assert task_response.json()["code"] == "FORBIDDEN"
