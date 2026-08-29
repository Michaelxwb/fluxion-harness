"""TASK-002（Phase 5）PostgresEncryptedSecretStore 双库契约测试。

S-02 / B-02（design §3.3 secret_credentials 表 + §16.3 key rotation）。

真实边界：
- S-02：真实 SQLite（文件库，进程级重建 = 新 store 实例）+ 真实 PostgreSQL
  （FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控，复用 local-pg-test-env）；
- B-02：真实 env 读取路径（FLUXION_SECRET_MASTER_KEY）；
- 契约：put/rotate/revoke/resolve/list_metadata 与 LocalEncryptedSecretStore 同形；
  ciphertext 字节存储非明文；key rotation 批量重加密经 key_id/cipher_version 可解。
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from fluxion.registry.schema import audit_logs, secret_credentials

# ---------------------------------------------------------------------------
# 双库引擎参数化（SQLite 恒有；PostgreSQL 门控——与 test_registry_store 同模式）
#


def _engine_params() -> list[object]:
    params: list[object] = [pytest.param("sqlite", id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param("postgres", id="postgres"))
    return params


@pytest.fixture(params=_engine_params())
async def engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncGenerator[tuple[AsyncEngine, str], None]:
    """返回 (engine, kind)。SQLite 用文件库（跨 store 实例持久）；PG 复用 fluxion_test。"""
    kind: str = request.param
    if kind == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'secrets.db'}")
    else:
        dsn = os.environ.get(
            "FLUXION_POSTGRES_DSN",
            "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
        )
        engine = create_async_engine(dsn)
    try:
        yield engine, kind
    finally:
        await engine.dispose()


def _master_key() -> bytes:
    return os.urandom(32)


async def _make_store(engine: AsyncEngine, master_key: bytes, key_id: str = "k1") -> PostgresEncryptedSecretStore:
    store = PostgresEncryptedSecretStore(engine=engine, master_key=master_key, key_id=key_id)
    await store.initialize()
    return store


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# S-02：put → rotate（master key 批量重加密）→ 重启 → resolve 一致


class TestS02SecretCredentialsContract:
    async def test_put_resolve_and_ciphertext_not_plaintext(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        tenant = _unique("tenant")
        name = _unique("api-key")

        ref = await store.put(tenant, name, "super-secret-value")
        assert ref == f"secret://{tenant}/{name}@1"

        resolved = await store.resolve(ref)
        assert resolved.value == "super-secret-value"
        assert resolved.version == "1"

        # 密文字节存储：表中 ciphertext 不含明文
        async with eng.connect() as conn:
            row = (
                await conn.execute(
                    select(secret_credentials).where(secret_credentials.c.ref == ref)
                )
            ).one()
        assert row.ciphertext != b"super-secret-value"
        assert b"super-secret-value" not in row.ciphertext
        assert len(row.nonce) == 12
        assert row.revoked is False

    async def test_rebuild_store_resolves_persisted_secret(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        """进程级重建（新 store 实例）→ resolve 一致（持久化，非内存态）。"""
        eng, _kind = engine
        master_key = _master_key()
        store = await _make_store(eng, master_key)
        tenant = _unique("tenant")
        name = _unique("db-password")

        ref = await store.put(tenant, name, "persisted-plaintext")

        rebuilt = await _make_store(eng, master_key)  # 模拟重启：新实例同 key
        resolved = await rebuilt.resolve(ref)
        assert resolved.value == "persisted-plaintext"

    async def test_version_rotate_creates_new_version(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        tenant = _unique("tenant")
        name = _unique("token")

        ref_v1 = await store.put(tenant, name, "v1-plaintext")
        ref_v2 = await store.rotate(ref_v1, "v2-plaintext")

        assert ref_v2 == f"secret://{tenant}/{name}@2"
        assert (await store.resolve(ref_v1)).value == "v1-plaintext"
        assert (await store.resolve(ref_v2)).value == "v2-plaintext"

    async def test_master_key_rotation_reencrypts_and_old_versions_decrypt(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        """§16.3 key rotation：按 key_id 解旧密 → 新密加密 → 批量 re-encrypt →
        revoke 旧 key；rotate 后旧密文（新 key 下）仍可解。"""
        eng, _kind = engine
        old_key = _master_key()
        # PG 契约门禁共享库表且注册表持久——key_id 取唯一值避免跨用例/跨运行残留
        old_id, new_id = _unique("k-old"), _unique("k-new")
        store = await _make_store(eng, old_key, key_id=old_id)
        tenant = _unique("tenant")
        n1, n2 = _unique("alpha"), _unique("beta")

        ref1 = await store.put(tenant, n1, "alpha-secret")
        ref2 = await store.put(tenant, n2, "beta-secret")

        new_key = _master_key()
        count = await store.rotate_master_key(
            new_key_id=new_id,
            new_key=new_key,
            actor_id="admin-1",
            request_id="req-rot-1",
        )
        assert count == 2

        # 旧 key 已 revoke：用旧 key 的 key_id 加密不可再发生（keyring 移除）
        assert store.active_key_id == new_id
        assert old_id not in store.keyring

        # rotate 后所有密文可解（key_id/cipher_version 已更新）
        assert (await store.resolve(ref1)).value == "alpha-secret"
        assert (await store.resolve(ref2)).value == "beta-secret"

        # key_id/cipher_version 落表
        async with eng.connect() as conn:
            rows = (
                await conn.execute(
                    select(secret_credentials).where(
                        secret_credentials.c.tenant_id == tenant
                    )
                )
            ).fetchall()
        assert {row.key_id for row in rows} == {new_id}
        assert all(row.cipher_version == "aes-256-gcm-v1" for row in rows)
        assert all(row.rotated_at is not None for row in rows)

        # rotation 进 AuditLog（规则 24）
        async with eng.connect() as conn:
            audit_row = (
                await conn.execute(
                    select(audit_logs).where(audit_logs.c.action == "secret.rotate_master_key")
                )
            ).fetchall()
        assert any(row.request_id == "req-rot-1" and row.tenant_id == tenant for row in audit_row)

        # 重启后新 store（新 key）仍可解
        rebuilt = await _make_store(eng, new_key, key_id=new_id)
        assert (await rebuilt.resolve(ref1)).value == "alpha-secret"

    async def test_master_key_rotation_survives_restart_via_registry(
        self, engine: tuple[AsyncEngine, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """review P1-1：旋转后重启（env 换新 key 材料、不传 key_id）→ 注册表
        active 行绑定 env 材料，既有 secret 仍可解——此前 key_id 无持久化事实源，
        from_env 默认 k1 → 重启后全量 secret_key_revoked 失效。"""
        from fluxion.registry.schema import secret_master_keys

        eng, _kind = engine
        old_key = _master_key()
        # PG 契约门禁各用例共享库表——key_id 取唯一值避免注册表跨用例残留
        old_id, new_id = _unique("k-old"), _unique("k-next")
        store = await _make_store(eng, old_key, key_id=old_id)
        tenant = _unique("tenant")
        name = _unique("secret")
        ref = await store.put(tenant, name, "rotation-secret")

        new_key = _master_key()
        await store.rotate_master_key(
            new_key_id=new_id,
            new_key=new_key,
            actor_id="admin-p11",
            request_id="req-p11",
        )

        # 注册表事实源：k-next 登记 active、k-old revoke（与重加密同事务）
        async with eng.connect() as conn:
            rows = {
                row.key_id: row.revoked_at
                for row in (
                    await conn.execute(select(secret_master_keys))
                ).fetchall()
            }
        assert rows.get(new_id) is None
        assert rows.get(old_id) is not None

        # 运维重启：env 换新 key 材料，不传 key_id
        monkeypatch.setenv(
            "FLUXION_SECRET_MASTER_KEY", base64.b64encode(new_key).decode()
        )
        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY_ID", raising=False)
        restarted = PostgresEncryptedSecretStore.from_env(engine=eng)
        await restarted.initialize()
        assert restarted.active_key_id == new_id
        assert (await restarted.resolve(ref)).value == "rotation-secret"

        # 显式配置已 revoke 的旧 key_id 重启 → fail-fast（旋转窗口明确失败）
        stale = PostgresEncryptedSecretStore.from_env(engine=eng, key_id=old_id)
        from fluxion.runtime.secrets import SecretProviderError

        with pytest.raises(SecretProviderError) as excinfo:
            await stale.initialize()
        assert excinfo.value.code == "secret_key_revoked"

    async def test_revoke_rejects_resolve(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        from fluxion.runtime.secrets import SecretProviderError

        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        tenant = _unique("tenant")
        name = _unique("leaked")

        ref = await store.put(tenant, name, "to-be-revoked")
        await store.revoke(ref)

        with pytest.raises(SecretProviderError, match="revoked"):
            await store.resolve(ref)

    async def test_resolve_missing_ref_raises(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        from fluxion.runtime.secrets import SecretProviderError

        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        with pytest.raises(SecretProviderError, match="not found"):
            await store.resolve("secret://no-tenant/no-name@1")

    async def test_list_metadata_tenant_scoped(
        self, engine: tuple[AsyncEngine, str]
    ) -> None:
        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        tenant_a, tenant_b = _unique("ta"), _unique("tb")

        await store.put(tenant_a, _unique("k"), "a-value")
        await store.put(tenant_b, _unique("k"), "b-value")

        items_a, total_a = await store.list_metadata(tenant_id=tenant_a, offset=0, limit=10)
        assert total_a == 1
        assert [item.tenant_id for item in items_a] == [tenant_a]
        assert items_a[0].provider == "postgres_encrypted"
        assert items_a[0].ref.startswith(f"secret://{tenant_a}/")

    async def test_put_timeout_bounded(self, engine: tuple[AsyncEngine, str]) -> None:
        from fluxion.runtime.secrets import SecretProviderError

        eng, _kind = engine
        store = await _make_store(eng, _master_key())
        with pytest.raises(SecretProviderError):
            await store.put(_unique("t"), _unique("k"), "x", timeout_ms=0)


# ---------------------------------------------------------------------------
# B-02：Master Key 外置 env 校验（fail-fast，不静默生成）


class TestB02MasterKeyEnvFailFast:
    async def test_missing_env_raises(self, engine: tuple[AsyncEngine, str], monkeypatch: pytest.MonkeyPatch) -> None:
        from fluxion.runtime.secrets import SecretProviderError

        eng, _kind = engine
        monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
        with pytest.raises(SecretProviderError) as excinfo:
            PostgresEncryptedSecretStore.from_env(engine=eng)
        assert excinfo.value.code == "secret_master_key_missing"

    async def test_wrong_length_env_raises(
        self, engine: tuple[AsyncEngine, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fluxion.runtime.secrets import SecretProviderError

        eng, _kind = engine
        # base64 编码的 16B key（≠32B）→ 明确报错，不静默生成
        monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(b"\x01" * 16).decode())
        with pytest.raises(SecretProviderError) as excinfo:
            PostgresEncryptedSecretStore.from_env(engine=eng)
        assert excinfo.value.code == "secret_master_key_invalid"

    async def test_valid_env_key_accepted(
        self, engine: tuple[AsyncEngine, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eng, _kind = engine
        monkeypatch.setenv(
            "FLUXION_SECRET_MASTER_KEY", base64.b64encode(_master_key()).decode()
        )
        store = PostgresEncryptedSecretStore.from_env(engine=eng)
        assert store is not None
