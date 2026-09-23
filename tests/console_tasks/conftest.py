"""tests/console_tasks 夹具：复用 Console 认证/租户夹具，并桥接真实 Worker HTTP。

`client`/`tenant`/`database_guard` 从 console_platform conftest 显式导入复用；
本文件额外提供 Worker Admin 客户端（ASGI 直连真实 Worker 应用）与 task schema 清理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from console_platform.conftest import (  # noqa: F401  (fixtures re-exported)
    ADMIN_PASSWORD,
    TenantContext,
    client,
    database_guard,
    tenant,
)
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.main import app as worker_app
from muad_console_platform.api.deps import get_worker_client
from muad_console_platform.infrastructure.worker_client import WorkerAdminClient
from muad_console_platform.main import app as console_app
from sqlalchemy import text

TOKEN = "console-worker-token"

WORKER_TENANT_CLEANUP = (
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
)


@pytest.fixture(autouse=True)
def internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


@pytest.fixture
async def worker_client() -> AsyncIterator[WorkerAdminClient]:
    client = WorkerAdminClient(
        "http://worker",
        service_token=TOKEN,
        transport=httpx.ASGITransport(app=worker_app),
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def worker_client_override(worker_client: WorkerAdminClient) -> Iterator[None]:
    console_app.dependency_overrides[get_worker_client] = lambda: worker_client
    try:
        yield
    finally:
        console_app.dependency_overrides.pop(get_worker_client, None)


@pytest.fixture
async def task_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """把 Console 租户同时用于 Worker task schema，并在用例后清理。"""

    async def cleanup() -> None:
        session_factory = worker_session_factory()
        for statement in WORKER_TENANT_CLEANUP:
            async with session_factory() as session:
                await session.execute(text(statement), {"t": tenant.tenant_id})
                await session.commit()

    await cleanup()
    try:
        yield tenant
    finally:
        await cleanup()
