"""PostgresEncryptedSecretStore：SecretProvider 生产实现（Phase 5 TASK-002）。

- 密文入 `secret_credentials` 表（AES-256-GCM 12B nonce，绝不存明文）；
- 与 `LocalEncryptedSecretStore` 同形 API（put/rotate/revoke/resolve/list_metadata）；
- engine 注入：SQLite（dev/契约）与 PostgreSQL（生产）跑同一套 Contract Test（规则 7）；
- Master Key 外置 env `FLUXION_SECRET_MASTER_KEY`（base64 32B），缺失/长度≠32
  启动 fail-fast，不静默生成（B-02 / RISK-P5-02）；
- Key rotation（remediation §16.3）：按 `key_id` 解旧密 → 新密加密 → 批量
  re-encrypt → revoke old key；rotation 进 AuditLog（规则 24）；
- active key_id 持久化事实源：`secret_master_keys` 注册表（review P1-1 修复）——
  rotate 与重加密同事务登记新 key/revoke 旧 key，重启后 `from_env` 按注册表
  active 行绑定 env 密钥材料，无需运维手动传 key_id；
- 多实例约束（review P1-2 缓解）：keyring 内存态、无 key 分发（rule 17 密钥
  材料不落库），旋转必须单写者执行 + 滚动重启；窗口期他实例 resolve 未持
  key 的行 → `secret_key_unavailable` 明确失败（不静默、不误报 revoked）；
- 全方法 timeout + fail policy（规则 18）：`asyncio.wait_for` deadline，
  超时/库错误 → SecretProviderError，不静默吞。

拆分（phase6 review 收尾，行数 <500）：AES-GCM 加解密原语在 `crypto.py`，
master key 批量重加密在 `rotation.py`——本文件只保留 store 生命周期/编排。
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar

from cryptography.exceptions import InvalidTag
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.plugins.secret.crypto import (
    CIPHER_VERSION,
    MASTER_KEY_BYTES,
    decrypt_secret,
    encrypt_secret,
)
from fluxion.plugins.secret.rotation import rotate_master_key_batch
from fluxion.registry.publish_sqlalchemy import insert_audit
from fluxion.registry.schema import audit_logs, secret_credentials, secret_master_keys
from fluxion.registry.store import AuditRecord
from fluxion.runtime.secrets import (
    ResolvedCredential,
    SecretMetadata,
    SecretProviderError,
)

_T = TypeVar("_T")


class PostgresEncryptedSecretStore:
    """加密 Secret 持久化 store（生产 PostgreSQL；契约测试复用 SQLite engine）。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        master_key: bytes,
        key_id: str | None = None,
        timeout_ms: int = 30_000,
    ) -> None:
        if len(master_key) != MASTER_KEY_BYTES:
            raise SecretProviderError(
                "secret_master_key_invalid",
                f"AES-256-GCM key must be {MASTER_KEY_BYTES} bytes",
            )
        self._engine = engine
        self._master_key = master_key
        # key_id 显式给定 → 直接绑定；None → initialize() 按注册表 active 行
        # 解析（review P1-1：旋转后重启不再依赖运维手动传 key_id）。
        self._configured_key_id = key_id
        if key_id is not None:
            self._keyring: dict[str, bytes] = {key_id: master_key}
            self._active_key_id = key_id
        else:
            self._keyring = {}
            self._active_key_id = ""
        self._revoked_key_ids: set[str] = set()
        self._timeout_ms = timeout_ms

    # ---- 构造入口 ----

    @classmethod
    def from_env(
        cls,
        *,
        engine: AsyncEngine,
        env_name: str = "FLUXION_SECRET_MASTER_KEY",
        key_id: str | None = None,
    ) -> PostgresEncryptedSecretStore:
        """Master Key 外置 env（base64 32B）；缺失/非法 → fail-fast（B-02）。

        key_id 解析顺序：显式参数 > env ``FLUXION_SECRET_MASTER_KEY_ID`` >
        （initialize 时）注册表 active 行 > "k1"。
        """
        raw = os.environ.get(env_name)
        if raw is None:
            raise SecretProviderError(
                "secret_master_key_missing", f"{env_name} is required"
            )
        try:
            key = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError) as exc:
            raise SecretProviderError(
                "secret_master_key_invalid", "master key must be base64"
            ) from exc
        explicit = key_id or os.environ.get("FLUXION_SECRET_MASTER_KEY_ID") or None
        return cls(engine=engine, master_key=key, key_id=explicit)

    # ---- 可观测（rotation 断言用只读视图）----

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def keyring(self) -> dict[str, bytes]:
        return dict(self._keyring)

    # ---- 生命周期 ----

    async def initialize(self) -> None:
        """幂等建表（secret_credentials + audit_logs + secret_master_keys）+ active key 解析。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: secret_credentials.create(sync_conn, checkfirst=True)
            )
            await conn.run_sync(
                lambda sync_conn: audit_logs.create(sync_conn, checkfirst=True)
            )
            await conn.run_sync(
                lambda sync_conn: secret_master_keys.create(sync_conn, checkfirst=True)
            )
        await self._resolve_active_key()

    async def _resolve_active_key(self) -> None:
        """active key_id 绑定：显式配置 > 注册表 active 行 > "k1"（首启默认）。

        显式配置且注册表标记 revoked → fail-fast（旋转窗口用旧 key 重启的
        明确失败，不静默）；显式配置不在注册表（首启/测试）→ 放行。
        """
        if self._configured_key_id is not None:
            if await self._key_revoked(self._configured_key_id):
                raise SecretProviderError(
                    "secret_key_revoked",
                    f"configured key_id {self._configured_key_id} is revoked",
                )
            self._active_key_id = self._configured_key_id
            self._keyring = {self._configured_key_id: self._master_key}
            return
        self._active_key_id = await self._read_active_key_id() or "k1"
        self._keyring = {self._active_key_id: self._master_key}

    async def _read_active_key_id(self) -> str | None:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(secret_master_keys.c.key_id)
                .where(secret_master_keys.c.revoked_at.is_(None))
                .order_by(secret_master_keys.c.created_at.desc())
                .limit(1)
            )
        active = row.scalar_one_or_none()
        return str(active) if active is not None else None

    async def _key_revoked(self, key_id: str) -> bool:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(secret_master_keys.c.revoked_at).where(
                    secret_master_keys.c.key_id == key_id
                )
            )
        revoked_at = row.scalar_one_or_none()
        return revoked_at is not None

    # ---- SPI（与 LocalEncryptedSecretStore 同形；audit 参数可选，规则 24）----

    async def put(
        self,
        tenant_id: str,
        name: str,
        plaintext: str,
        timeout_ms: int | None = None,
        *,
        actor_id: str = "system",
        request_id: str = "req_unspecified",
        trace_id: str | None = None,
    ) -> str:
        ref = await self._with_deadline(
            self._put(tenant_id, name, plaintext), timeout_ms, f"put {name}"
        )
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            trace_id=trace_id,
            action="secret.put",
            target_id=ref,
            after={"name": name},
        )
        return ref

    async def rotate(
        self, ref: str, plaintext: str, timeout_ms: int | None = None
    ) -> str:
        return await self._with_deadline(
            self._rotate(ref, plaintext), timeout_ms, f"rotate {ref}"
        )

    async def revoke(
        self,
        ref: str,
        timeout_ms: int | None = None,
        *,
        actor_id: str = "system",
        request_id: str = "req_unspecified",
        trace_id: str | None = None,
    ) -> None:
        await self._with_deadline(self._revoke(ref), timeout_ms, f"revoke {ref}")
        tenant_id = ref[len("secret://") :].split("/", 1)[0]
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            trace_id=trace_id,
            action="secret.revoke",
            target_id=ref,
        )

    async def resolve(
        self, ref: str, timeout_ms: int | None = None
    ) -> ResolvedCredential:
        return await self._with_deadline(self._resolve(ref), timeout_ms, f"resolve {ref}")

    async def list_metadata(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        timeout_ms: int | None = None,
    ) -> tuple[list[SecretMetadata], int]:
        return await self._with_deadline(
            self._list_metadata(tenant_id, offset, limit),
            timeout_ms,
            f"list_metadata {tenant_id}",
        )

    # ---- Master key rotation（§16.3）----

    async def rotate_master_key(
        self,
        *,
        new_key_id: str,
        new_key: bytes,
        actor_id: str,
        request_id: str,
        trace_id: str | None = None,
        timeout_ms: int = 120_000,
    ) -> int:
        """批量重加密：解旧密 → 新密加密 → 更新 key_id/cipher_version → revoke 旧 key。

        rotation 按 tenant 分组写入 AuditLog（规则 24，关联 trace_id）；返回重加密记录数。
        重加密事务逻辑在 `rotation.rotate_master_key_batch`；本方法负责 keyring 守卫与收口。
        """
        if len(new_key) != MASTER_KEY_BYTES:
            raise SecretProviderError(
                "secret_master_key_invalid",
                f"AES-256-GCM key must be {MASTER_KEY_BYTES} bytes",
            )
        if new_key_id in self._keyring:
            raise SecretProviderError(
                "secret_key_conflict", f"key_id {new_key_id} already exists"
            )
        old_key_id = self._active_key_id
        if old_key_id not in self._keyring:
            # review P1-2 缓解：他实例已旋转（本实例未持新 key）→ 明确失败，
            # 不以 KeyError/错密文静默损坏。
            raise SecretProviderError(
                "secret_key_unavailable",
                f"active key {old_key_id} not held by this instance "
                "(rotated elsewhere? single-writer rotation + rolling restart required)",
            )
        now = datetime.now(UTC)
        count = await self._with_deadline(
            rotate_master_key_batch(
                engine=self._engine,
                old_key_id=old_key_id,
                new_key_id=new_key_id,
                old_key=self._keyring[old_key_id],
                new_key=new_key,
                actor_id=actor_id,
                request_id=request_id,
                trace_id=trace_id,
                now=now,
            ),
            timeout_ms,
            f"rotate_master_key {old_key_id}->{new_key_id}",
        )
        # 事务提交成功后 revoke 旧 key（keyring 收口：旧 key 不可再加密/解密）
        self._revoked_key_ids.add(old_key_id)
        self._keyring = {new_key_id: new_key}
        self._active_key_id = new_key_id
        return count

    # ---- 内部实现（deadline 内执行）----

    def _active_key(self) -> bytes:
        """当前 active key 材料；keyring 未解析（未 initialize）→ 明确失败。"""
        key = self._keyring.get(self._active_key_id)
        if key is None:
            raise SecretProviderError(
                "secret_key_unavailable",
                "master key not resolved (initialize required)",
            )
        return key

    async def _put(self, tenant_id: str, name: str, plaintext: str) -> str:
        version = await self._next_version(tenant_id, name)
        ref = f"secret://{tenant_id}/{name}@{version}"
        nonce, ciphertext = encrypt_secret(self._active_key(), ref, plaintext)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(secret_credentials).values(
                    tenant_id=tenant_id,
                    ref=ref,
                    name=name,
                    version=version,
                    nonce=nonce,
                    ciphertext=ciphertext,
                    key_id=self._active_key_id,
                    cipher_version=CIPHER_VERSION,
                    revoked=False,
                )
            )
        return ref

    async def _rotate(self, ref: str, plaintext: str) -> str:
        record = await self._record(ref)
        version = await self._next_version(record.tenant_id, record.name)
        new_ref = f"secret://{record.tenant_id}/{record.name}@{version}"
        nonce, ciphertext = encrypt_secret(self._active_key(), new_ref, plaintext)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(secret_credentials).values(
                    tenant_id=record.tenant_id,
                    ref=new_ref,
                    name=record.name,
                    version=version,
                    nonce=nonce,
                    ciphertext=ciphertext,
                    key_id=self._active_key_id,
                    cipher_version=CIPHER_VERSION,
                    revoked=False,
                )
            )
        return new_ref

    async def _revoke(self, ref: str) -> None:
        await self._record(ref)  # 不存在 → secret_not_found
        async with self._engine.begin() as conn:
            await conn.execute(
                update(secret_credentials)
                .where(secret_credentials.c.ref == ref)
                .values(revoked=True)
            )

    async def _resolve(self, ref: str) -> ResolvedCredential:
        record = await self._record(ref)
        if record.revoked:
            raise SecretProviderError("secret_revoked", f"{ref} is revoked")
        key = self._keyring.get(record.key_id)
        if key is None:
            raise SecretProviderError(
                "secret_key_unavailable",
                f"{ref} references key {record.key_id} not held by this instance "
                "(rotation in progress or master key mismatch)",
            )
        try:
            plaintext = decrypt_secret(key, ref, record.nonce, record.ciphertext)
        except InvalidTag as exc:
            raise SecretProviderError(
                "secret_decrypt_failed", f"{ref} decrypt failed"
            ) from exc
        return ResolvedCredential(value=plaintext.decode("utf-8"), version=record.version)

    async def _list_metadata(
        self, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[SecretMetadata], int]:
        async with self._engine.connect() as conn:
            total_row: Any = await conn.execute(
                select(func.count())
                .select_from(secret_credentials)
                .where(secret_credentials.c.tenant_id == tenant_id)
            )
            total = int(total_row.scalar_one())
            rows = await conn.execute(
                select(secret_credentials)
                .where(secret_credentials.c.tenant_id == tenant_id)
                .order_by(secret_credentials.c.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            records = rows.fetchall()
        return (
            [
                SecretMetadata(
                    ref=record.ref,
                    tenant_id=record.tenant_id,
                    provider="postgres_encrypted",
                    version=record.version,
                    revoked=record.revoked,
                    created_at=record.created_at,
                )
                for record in records
            ],
            total,
        )

    async def _audit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request_id: str,
        trace_id: str | None,
        action: str,
        target_id: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        """高影响 secret 操作进 AuditLog（规则 24，关联 request_id/trace_id/tenant_id）。"""
        async with self._engine.begin() as conn:
            await insert_audit(
                conn,
                AuditRecord(
                    audit_id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    request_id=request_id,
                    action=action,
                    target_type="secret",
                    target_id=target_id,
                    before=before,
                    after=after,
                    trace_id=trace_id,
                ),
            )

    async def _record(self, ref: str) -> Any:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(secret_credentials).where(secret_credentials.c.ref == ref)
            )
            record = row.fetchone()
        if record is None:
            raise SecretProviderError("secret_not_found", f"{ref} not found")
        return record

    async def _next_version(self, tenant_id: str, name: str) -> str:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(secret_credentials.c.version).where(
                    secret_credentials.c.tenant_id == tenant_id,
                    secret_credentials.c.name == name,
                )
            )
            versions = [int(version) for version in row.scalars().all()]
        return str(max(versions, default=0) + 1)

    async def _with_deadline(
        self, coro: Coroutine[Any, Any, _T], timeout_ms: int | None, label: str
    ) -> _T:
        deadline = self._timeout_ms if timeout_ms is None else timeout_ms
        try:
            return await asyncio.wait_for(coro, timeout=deadline / 1000)
        except TimeoutError as error:
            raise SecretProviderError(
                "secret_timeout", f"{label} timed out ({deadline}ms)"
            ) from error
        except SQLAlchemyError as error:
            raise SecretProviderError(
                "secret_store_error", f"{label} failed: {error}"
            ) from error
