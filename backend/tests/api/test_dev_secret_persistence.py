"""dev Secret 落库回归：重启不丢密钥（ADR-A007 PG-Only，与生产同形态）。

真实边界：真实 dev bundle 装配（PG）+ 真实 HTTP 创建凭据 +
真实 AES-256-GCM 加密行；“重启”以重建 app + 新 engine 模拟。
密钥一律经 FLUXION_SECRET_MASTER_KEY 显式给（文件钥匙已删）。
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from tests.console_helpers import tenant_headers
from tests.runtime_helpers import TEST_POSTGRES_DSN


def _app(tmp_path, dsn: str):  # type: ignore[no-untyped-def]
    from fluxion.api.dev_bundle import create_dev_bundle_app

    console_dist = tmp_path / "console"
    chat_dist = tmp_path / "chat"
    console_dist.mkdir(exist_ok=True)
    chat_dist.mkdir(exist_ok=True)
    return create_dev_bundle_app(registry_dsn=dsn, console_dist=console_dist, chat_dist=chat_dist)


def _env_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    raw = os.urandom(32)
    monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(raw).decode())
    return raw


class TestDevBundleOnPostgres:
    @pytest.mark.asyncio
    async def test_pg_dsn_uses_postgres_store_and_key_required(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fluxion.registry import PostgreSQLRegistryStore

        dsn = TEST_POSTGRES_DSN
        # 无显式 key 时 fail-fast（不允许静默随机钥匙写 PG）。
        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FLUXION_SECRET_MASTER_KEY"):
            _app(tmp_path, dsn)

        _env_key(monkeypatch)
        app = _app(tmp_path, dsn)
        assert isinstance(app.state.runtime_service._store, PostgreSQLRegistryStore)
        await app.state.secret_store.initialize()
        ref = await app.state.secret_store.put("tenant-pg-dev", "pg-key", "sk-pg-sentinel")
        resolved = await app.state.secret_store.resolve(ref)
        assert resolved.value == "sk-pg-sentinel"


class TestDevMasterKeyExplicit:
    def test_env_key_honored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        raw = _env_key(monkeypatch)
        assert _dev_master_key() == raw

    def test_missing_key_fail_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FLUXION_SECRET_MASTER_KEY"):
            _dev_master_key()

    def test_bad_key_fail_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.api.dev_bundle import _dev_master_key

        monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", "!!!not-base64!!!")
        with pytest.raises(RuntimeError, match="base64"):
            _dev_master_key()
        monkeypatch.setenv(
            "FLUXION_SECRET_MASTER_KEY", base64.b64encode(b"short").decode()
        )
        with pytest.raises(RuntimeError, match="32 bytes"):
            _dev_master_key()


class TestDevSecretSurvivesRestart:
    @pytest.mark.asyncio
    async def test_create_resolve_across_rebuilds(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _env_key(monkeypatch)
        monkeypatch.delenv("FLUXION_MODEL_API_KEY", raising=False)
        monkeypatch.delenv("FLUXION_MCP_TOKEN", raising=False)
        dsn = TEST_POSTGRES_DSN
        name = f"restart-key-{uuid.uuid4().hex[:8]}"
        # ASGITransport 不触发 lifespan：secret initialize 幂等建表
        #（生产由 scripts/init_db.py 建表；Registry 表由 session 夹具建）。
        app1 = _app(tmp_path, dsn)
        assert isinstance(app1.state.secret_store, PostgresEncryptedSecretStore)
        await app1.state.secret_store.initialize()
        async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://dev") as client:
            created = await client.post(
                "/api/v1/credentials",
                json={"name": name, "secret": "sk-restart-sentinel", "purpose": "model"},
                headers=tenant_headers(request_id="req-create"),
            )
            assert created.status_code == 200, created.text
            ref = created.json()["data"]["spec"]["secret_ref"]

        # “重启”：全新 app + 全新 engine（同 DSN + 同显式 key）。
        app2 = _app(tmp_path, dsn)
        await app2.state.secret_store.initialize()
        try:
            resolved = await app2.state.secret_store.resolve(ref)
            assert resolved.value == "sk-restart-sentinel"
        finally:
            await app2.state.secret_store._engine.dispose()
            await app1.state.secret_store._engine.dispose()
