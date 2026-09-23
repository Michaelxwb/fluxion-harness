"""B-126：Console → Worker 有界 HTTP 客户端（设计 §3.4）。

真实边界：Console HTTP client → 真实 Worker HTTP（ASGI）→ 真实 PostgreSQL；
不 mock 封套与分页，超时/不可达显式映射且不泄露上游敏感正文。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.main import app as worker_app
from muad_api import AppError
from muad_console_platform.infrastructure.worker_client import WorkerAdminClient
from sqlalchemy import text

TOKEN = "console-worker-token"
TENANT = "test-console-worker"
SENSITIVE = "upstream-secret-body-value"


@pytest.fixture(autouse=True)
def internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
async def clean_tenant() -> AsyncGenerator[None, None]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM task.task_event WHERE tenant_id = :t"), {"t": TENANT}
        )
        await session.execute(
            text("DELETE FROM task.task_execution WHERE tenant_id = :t"), {"t": TENANT}
        )
        await session.commit()
    yield
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM task.task_event WHERE tenant_id = :t"), {"t": TENANT}
        )
        await session.execute(
            text("DELETE FROM task.task_execution WHERE tenant_id = :t"), {"t": TENANT}
        )
        await session.commit()


async def _persist_task(*, status: str = "QUEUED") -> uuid.UUID:
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    async with get_session_factory()() as session:
        async with session.begin():
            session.add(
                TaskExecution(
                    id=task_id,
                    tenant_id=TENANT,
                    agent_id=uuid.uuid4(),
                    actor_user_id=uuid.uuid4(),
                    intent_key="policy_check",
                    skill_id=uuid.uuid4(),
                    skill_artifact_id=uuid.uuid4(),
                    trigger_type="IMMEDIATE",
                    execution_mode="ASYNC",
                    task_type="SKILL",
                    status=status,
                    input_json={},
                    execution_snapshot_schema_version=1,
                    execution_snapshot_json={"schema_version": 1},
                    snapshot_hash="sha256:" + "b" * 64,
                    idempotency_key=f"console-{task_id}",
                    priority=100,
                    attempt=0,
                    max_attempts=3,
                    not_before=now,
                    deadline_at=now + timedelta(hours=1),
                    delivery_mode="NONE",
                    delivery_status="NONE",
                    delivery_key=f"task:{task_id}:final",
                    delivery_attempts=0,
                )
            )
    return task_id


def _asgi_client() -> WorkerAdminClient:
    return WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.ASGITransport(app=worker_app),
    )


async def test_b126_list_and_detail_keep_envelope_and_pagination() -> None:
    first = await _persist_task()
    second = await _persist_task()
    client = _asgi_client()
    try:
        listed = await client.list_tasks(tenant_id=TENANT, page=1, page_size=1)
        assert listed["total"] == 2
        assert len(listed["items"]) == 1
        assert {"page", "page_size", "total", "items"} <= set(listed)
        assert listed["items"][0]["task_id"] in {str(first), str(second)}

        detail = await client.get_task(tenant_id=TENANT, task_id=first)
        assert detail["task_id"] == str(first)
        assert detail["status"] == "QUEUED"
        assert detail["timeline"] == []
    finally:
        await client.aclose()


async def test_b126_business_error_code_is_preserved() -> None:
    client = _asgi_client()
    try:
        with pytest.raises(AppError) as excinfo:
            await client.get_task(tenant_id=TENANT, task_id=uuid.uuid4())
        assert excinfo.value.code == "COMMON_NOT_FOUND"
    finally:
        await client.aclose()


async def test_b126_cancel_and_schedule_admin_routes() -> None:
    task_id = await _persist_task()
    client = _asgi_client()
    try:
        cancelled = await client.cancel_task(tenant_id=TENANT, task_id=task_id)
        assert cancelled == {
            "task_id": str(task_id),
            "status": "CANCELLED",
            "cancel_requested": True,
        }
        schedules = await client.list_schedules(tenant_id=TENANT)
        assert schedules["total"] == 0
    finally:
        await client.aclose()


async def test_b126_identity_and_locale_headers_are_propagated() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"code": "0", "msg": "ok", "data": {"items": [], "total": 0}},
        )

    client = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.MockTransport(handler),
        locale="en-US",
    )
    try:
        await client.list_tasks(tenant_id=TENANT, trace_id="trace-1")
    finally:
        await client.aclose()

    headers = captured[0].headers
    assert headers["X-Tenant-Id"] == TENANT
    assert headers["X-Internal-Service"] == TOKEN
    assert headers["Accept-Language"] == "en-US"
    assert headers["X-Trace-Id"] == "trace-1"


async def test_b126_unreachable_and_timeout_are_explicit() -> None:
    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    unreachable = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.MockTransport(connect_error),
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await unreachable.list_tasks(tenant_id=TENANT)
        assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
    finally:
        await unreachable.aclose()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    slow = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.MockTransport(timeout),
        timeout_sec=0.1,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await slow.list_tasks(tenant_id=TENANT)
        assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
    finally:
        await slow.aclose()


async def test_b126_upstream_sensitive_body_is_not_leaked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": "COMMON_INTERNAL_ERROR", "msg": SENSITIVE})

    client = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.MockTransport(failing),
    )
    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(AppError) as excinfo:
                await client.list_tasks(tenant_id=TENANT)
    finally:
        await client.aclose()

    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
    assert SENSITIVE not in str(excinfo.value)
    assert SENSITIVE not in str(excinfo.value.message_args)
    assert all(SENSITIVE not in record.getMessage() for record in caplog.records)


async def test_b126_envelope_without_data_is_internal_error() -> None:
    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "ok"})

    client = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.MockTransport(malformed),
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.list_tasks(tenant_id=TENANT)
        assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
    finally:
        await client.aclose()
