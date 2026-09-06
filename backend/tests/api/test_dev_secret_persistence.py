"""dev Secret 落库回归：重启不丢密钥（与生产同形态）。

真实边界：真实 dev bundle 装配（file-SQLite）+ 真实 HTTP 创建凭据 +
真实 AES-256-GCM 加密行；“重启”以重建 app + 新 engine 模拟。
此前 dev 用纯内存 store，重启后 Registry 引用悬空（secret_not_found）。
"""

from __future__ import annotations

import base64
import os
import stat

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from tests.console_helpers import tenant_headers


def _app(tmp_path, dsn: str):  # type: ignore[no-untyped-def]
    from fluxion.api.dev_bundle import create_dev_bundle_app

    console_dist = tmp_path / "console"
    chat_dist = tmp_path / "chat"
    console_dist.mkdir(exist_ok=True)
    chat_dist.mkdir(exist_ok=True)
    return create_dev_bundle_app(registry_dsn=dsn, console_dist=console_dist, chat_dist=chat_dist)


def _pg_available() -> bool:
    from urllib.parse import urlparse
    import socket

    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
    )
    parsed = urlparse(dsn)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _pg_available(), reason="PG 不可达")
class TestDevBundleOnPostgres:
    @pytest.mark.asyncio
    async def test_pg_dsn_uses_postgres_store_and_key_required(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        from fluxion.registry import PostgreSQLRegistryStore

        dsn = os.environ.get(
            "FLUXION_POSTGRES_DSN",
            "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
        )
        # 无显式 key 时 fail-fast（不允许静默随机钥匙写 PG）。
        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FLUXION_SECRET_MASTER_KEY"):
            _app(tmp_path, dsn)

        monkeypatch.setenv(
            "FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
        )
        app = _app(tmp_path, dsn)
        assert isinstance(app.state.runtime_service._store, PostgreSQLRegistryStore)
        await app.state.secret_store.initialize()
        ref = await app.state.secret_store.put("tenant-pg-dev", "pg-key", "sk-pg-sentinel")
        resolved = await app.state.secret_store.resolve(ref)
        assert resolved.value == "sk-pg-sentinel"


class TestDevMasterKey:
    def test_env_key_honored(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        raw = os.urandom(32)
        monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(raw).decode())
        assert _dev_master_key(f"sqlite+aiosqlite:///{tmp_path}/dev.db") == raw

    def test_key_file_generated_once_with_strict_mode(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/dev.db"
        first = _dev_master_key(dsn)
        key_file = tmp_path / ".fluxion-dev-master-key"
        assert key_file.exists()
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert _dev_master_key(dsn) == first

    def test_memory_dsn_ephemeral(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        assert _dev_master_key("sqlite+aiosqlite:///:memory:") != _dev_master_key(
            "sqlite+aiosqlite:///:memory:"
        )


class TestDevSecretSurvivesRestart:
    @pytest.mark.asyncio
    async def test_create_resolve_across_rebuilds(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        monkeypatch.delenv("FLUXION_MODEL_API_KEY", raising=False)
        monkeypatch.delenv("FLUXION_MCP_TOKEN", raising=False)
        dsn = f"sqlite+aiosqlite:///{tmp_path}/dev.db"
        # ASGITransport 不触发 lifespan：先建 registry + secret 两套表
        #（生产由 scripts/init_db.py 建表，dev lifespan 同义）。
        from fluxion.registry import SQLiteRegistryStore

        bootstrap = SQLiteRegistryStore(dsn)
        await bootstrap.initialize()
        await bootstrap.close()

        app1 = _app(tmp_path, dsn)
        assert isinstance(app1.state.secret_store, PostgresEncryptedSecretStore)
        await app1.state.secret_store.initialize()
        async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://dev") as client:
            created = await client.post(
                "/api/v1/credentials",
                json={"name": "restart-key", "secret": "sk-restart-sentinel", "purpose": "model"},
                headers=tenant_headers(request_id="req-create"),
            )
            assert created.status_code == 200, created.text
            ref = created.json()["data"]["spec"]["secret_ref"]

        # “重启”：全新 app + 全新 engine（同 DSN 文件 + 同 key 文件）。
        app2 = _app(tmp_path, dsn)
        await app2.state.secret_store.initialize()
        try:
            resolved = await app2.state.secret_store.resolve(ref)
            assert resolved.value == "sk-restart-sentinel"
        finally:
            await app2.state.secret_store._engine.dispose()
            await app1.state.secret_store._engine.dispose()
