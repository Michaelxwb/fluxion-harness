"""用户查看 360 回归：dev/production 装配必须注入 UserDomainService。

真实边界：真实 bundle 装配（dev/prod 同 PG，production 门控 PG）+
真实 HTTP（ASGITransport）+ 真实 UserDomainService + 真实 Store。
此前两 bundle 均未注入，`/admin/users/*` 全 503。
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from tests.console_helpers import tenant_headers
from tests.runtime_helpers import TEST_POSTGRES_DSN

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_dev_bundle_admin_users_wired(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import base64

    from fluxion.api.dev_bundle import create_dev_bundle_app
    from fluxion.registry import PostgreSQLRegistryStore

    # ADR-A007：dev bundle 要求显式 master key。
    monkeypatch.setenv(
        "FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    console_dist = tmp_path / "console"
    chat_dist = tmp_path / "chat"
    console_dist.mkdir()
    chat_dist.mkdir()
    dsn = TEST_POSTGRES_DSN
    # ASGITransport 不触发 lifespan：先经同 DSN store 初始化 schema
    #（生产由 scripts/init_db.py 建表，dev lifespan 同义）。
    bootstrap = PostgreSQLRegistryStore(dsn, reset_on_initialize=True)
    await bootstrap.initialize()
    await bootstrap.close()
    app = create_dev_bundle_app(
        registry_dsn=dsn,
        console_dist=console_dist,
        chat_dist=chat_dist,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://dev") as client:
        created = await client.post(
            "/admin/users",
            json={"platform_user_id": "alice", "display_name": "Alice"},
            headers=tenant_headers(request_id="req-wire-create"),
        )
        assert created.status_code == 200, created.text
        view = await client.get(
            "/admin/users/alice/360",
            headers=tenant_headers(request_id="req-wire-360"),
        )
        assert view.status_code == 200, view.text
        assert view.json()["data"]["identity"]["platform_user_id"] == "alice"


@pytest.mark.skipif(not _pg_available(), reason="PG 不可达")
@pytest.mark.asyncio
async def test_production_bundle_admin_users_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    from fluxion.api.production_bundle import create_production_bundle_app_from_env

    monkeypatch.setenv("FLUXION_DATABASE_URL", _PG_DSN)
    monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("FLUXION_RUNTIME_SERVICE_URL", "http://fluxion-runtime:8000")
    from sqlalchemy.ext.asyncio import create_async_engine

    from fluxion.registry.schema import metadata

    engine = create_async_engine(_PG_DSN)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True))
    await engine.dispose()
    app = create_production_bundle_app_from_env()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://prod") as client:
        view = await client.get(
            "/admin/users/alice/360",
            headers=tenant_headers(request_id="req-wire-360-pg"),
        )
        # 装配缺失时为 503 requires user domain service；接线后不再是该错误
        #（用户不存在则 404 user_not_bound，均为 UserDomainService 真实行为）。
        assert view.status_code != 503, view.text
