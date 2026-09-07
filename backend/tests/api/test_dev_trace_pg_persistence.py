"""Dev bundle trace 走 PG（统一持久化）：重启前后执行记录可读。

真实边界：create_dev_bundle_app lifespan → PG trace_records → GET /api/v1/runs。
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from fluxion.api.dev_bundle import create_dev_bundle_app
from fluxion.repositories.trace_store import PostgresTraceStore
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent
from fluxion.runtime.tracing import TraceRecord
from tests.runtime_helpers import TEST_POSTGRES_DSN

TENANT = "dev"  # dev bundle 中间件 pin 租户（忽略 X-Tenant-ID header）


def _record() -> TraceRecord:
    exec_id = f"exec-{uuid.uuid4().hex[:8]}"
    return TraceRecord(
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
        execution_id=exec_id,
        tenant_id=TENANT,
        runtime_profile_id="runtime-main",
        runtime_profile_version="7",
        snapshot=ExecutionSnapshot(
            execution_id=exec_id,
            tenant_id=TENANT,
            user_id="user-1",
            runtime_profile_id="runtime-main",
            runtime_profile_version="7",
            model_resolution={"routes": []},
            trace_id=f"trace-{exec_id}",
            created_at=datetime.now(UTC),
        ),
        events=(
            TraceEvent(
                name="model.response",
                tenant_id=TENANT,
                execution_id=exec_id,
                trace_id=f"trace-{exec_id}",
                attributes={},
            ),
        ),
        latency_ms=42.0,
        error=None,
        model={"provider": "dev.echo", "latency_ms": 10},
        tools=({"tool": "demo", "ok": True},),
    )


def _bundle(tmp_path) -> AsyncClient:  # type: ignore[no-untyped-def]
    console_dist = tmp_path / "console"
    chat_dist = tmp_path / "chat"
    console_dist.mkdir(parents=True, exist_ok=True)
    chat_dist.mkdir(parents=True, exist_ok=True)
    app = create_dev_bundle_app(
        registry_dsn=TEST_POSTGRES_DSN,
        console_dist=console_dist,
        chat_dist=chat_dist,
    )
    return app  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_dev_traces_survive_bundle_restart(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(b"t" * 32).decode())
    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        store = PostgresTraceStore(engine=engine)
        await store.initialize()
        record = _record()
        await store.append(record)

        headers = {"X-Tenant-ID": TENANT}
        app = _bundle(tmp_path)
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://dev") as client,
            app.router.lifespan_context(app),
        ):
            first = await client.get("/api/v1/runs?page=1&page_size=10", headers=headers)
            assert first.status_code == 200
            items = first.json()["data"]["items"]
            assert any(
                item["execution_id"] == record.execution_id
                and item["latency_ms"] == 42.0
                for item in items
            ), f"bundle lifespan 内应读到 PG trace: {first.json()}"

        app2 = _bundle(tmp_path)
        async with (
            AsyncClient(transport=ASGITransport(app=app2), base_url="http://dev") as client2,
            app2.router.lifespan_context(app2),
        ):
            second = await client2.get(
                "/api/v1/runs?page=1&page_size=10", headers=headers
            )
            assert second.status_code == 200
            items2 = second.json()["data"]["items"]
            assert any(
                item["execution_id"] == record.execution_id for item in items2
            ), "bundle 重启后 PG trace 仍可读"
    finally:
        await engine.dispose()
