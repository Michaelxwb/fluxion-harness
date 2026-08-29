"""PostgresEncryptedSecretStore：SecretProvider 生产实现（Phase 5 TASK-002）。

- 密文入 `secret_credentials` 表（AES-256-GCM 12B nonce，绝不存明文）；
- 与 `LocalEncryptedSecretStore` 同形 API（put/rotate/revoke/resolve/list_metadata）；
- engine 注入：SQLite（dev/契约）与 PostgreSQL（生产）跑同一套 Contract Test（规则 7）；
- Master Key 外置 env `FLUXION_SECRET_MASTER_KEY`（base64 32B），缺失/长度≠32
  启动 fail-fast，不静默生成（B-02 / RISK-P5-02）；
- Key rotation（remediation §16.3）：按 `key_id` 解旧密 → 新密加密 → 批量
  re-encrypt → revoke old key；rotation 进 AuditLog（规则 24）；
- 全方法 timeout + fail policy（规则 18）：`asyncio.wait_for` deadline，
  超时/库错误 → SecretProviderError，不静默吞。
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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.publish_sqlalchemy import insert_audit
from fluxion.registry.schema import audit_logs, secret_credentials
from fluxion.registry.store import AuditRecord
from fluxion.runtime.secrets import (
    ResolvedCredential,
    SecretMetadata,
    SecretProviderError,
)

_CIPHER_VERSION = "aes-256-gcm-v1"
_NONCE_BYTES = 12
_MASTER_KEY_BYTES = 32

_T = TypeVar("_T")


class PostgresEncryptedSecretStore:
    """加密 Secret 持久化 store（生产 PostgreSQL；契约测试复用 SQLite engine）。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        master_key: bytes,
        key_id: str = "k1",
        timeout_ms: int = 30_000,
    ) -> None:
        if len(master_key) != _MASTER_KEY_BYTES:
            raise SecretProviderError(
                "secret_master_key_invalid",
                f"AES-256-GCM key must be {_MASTER_KEY_BYTES} bytes",
            )
        self._engine = engine
        self._keyring: dict[str, bytes] = {key_id: master_key}
        self._active_key_id = key_id
        self._revoked_key_ids: set[str] = set()
        self._timeout_ms = timeout_ms

    # ---- 构造入口 ----

    @classmethod
    def from_env(
        cls,
        *,
        engine: AsyncEngine,
        env_name: str = "FLUXION_SECRET_MASTER_KEY",
        key_id: str = "k1",
    ) -> PostgresEncryptedSecretStore:
        """Master Key 外置 env（base64 32B）；缺失/非法 → fail-fast（B-02）。"""
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
        return cls(engine=engine, master_key=key, key_id=key_id)

    # ---- 可观测（rotation 断言用只读视图）----

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def keyring(self) -> dict[str, bytes]:
        return dict(self._keyring)

    # ---- 生命周期 ----

    async def initialize(self) -> None:
        """幂等建表（secret_credentials + audit_logs——rotation 写审计）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: secret_credentials.create(sync_conn, checkfirst=True)
            )
            await conn.run_sync(
                lambda sync_conn: audit_logs.create(sync_conn, checkfirst=True)
            )

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
        """
        if len(new_key) != _MASTER_KEY_BYTES:
            raise SecretProviderError(
                "secret_master_key_invalid",
                f"AES-256-GCM key must be {_MASTER_KEY_BYTES} bytes",
            )
        if new_key_id in self._keyring:
            raise SecretProviderError(
                "secret_key_conflict", f"key_id {new_key_id} already exists"
            )
        old_key_id = self._active_key_id
        now = datetime.now(UTC)
        count = await self._with_deadline(
            self._rotate_master_key(
                old_key_id=old_key_id,
                new_key_id=new_key_id,
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

    async def _put(self, tenant_id: str, name: str, plaintext: str) -> str:
        version = await self._next_version(tenant_id, name)
        ref = f"secret://{tenant_id}/{name}@{version}"
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._keyring[self._active_key_id]).encrypt(
            nonce, plaintext.encode("utf-8"), ref.encode()
        )
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
                    cipher_version=_CIPHER_VERSION,
                    revoked=False,
                )
            )
        return ref

    async def _rotate(self, ref: str, plaintext: str) -> str:
        record = await self._record(ref)
        version = await self._next_version(record.tenant_id, record.name)
        new_ref = f"secret://{record.tenant_id}/{record.name}@{version}"
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._keyring[self._active_key_id]).encrypt(
            nonce, plaintext.encode("utf-8"), new_ref.encode()
        )
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
                    cipher_version=_CIPHER_VERSION,
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
                "secret_key_revoked",
                f"{ref} references revoked key {record.key_id}",
            )
        try:
            plaintext = AESGCM(key).decrypt(record.nonce, record.ciphertext, ref.encode())
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

    async def _rotate_master_key(
        self,
        *,
        old_key_id: str,
        new_key_id: str,
        new_key: bytes,
        actor_id: str,
        request_id: str,
        trace_id: str | None,
        now: datetime,
    ) -> int:
        old_cipher = AESGCM(self._keyring[old_key_id])
        new_cipher = AESGCM(new_key)
        async with self._engine.begin() as conn:
            rows = (
                await conn.execute(
                    select(secret_credentials).where(
                        secret_credentials.c.key_id == old_key_id
                    )
                )
            ).fetchall()
            rotated_by_tenant: dict[str, int] = {}
            for record in rows:
                try:
                    plaintext = old_cipher.decrypt(
                        record.nonce, record.ciphertext, record.ref.encode()
                    )
                except InvalidTag as exc:
                    raise SecretProviderError(
                        "secret_decrypt_failed", f"{record.ref} decrypt failed"
                    ) from exc
                nonce = os.urandom(_NONCE_BYTES)
                ciphertext = new_cipher.encrypt(nonce, plaintext, record.ref.encode())
                await conn.execute(
                    update(secret_credentials)
                    .where(secret_credentials.c.ref == record.ref)
                    .values(
                        nonce=nonce,
                        ciphertext=ciphertext,
                        key_id=new_key_id,
                        cipher_version=_CIPHER_VERSION,
                        rotated_at=now,
                    )
                )
                rotated_by_tenant[record.tenant_id] = (
                    rotated_by_tenant.get(record.tenant_id, 0) + 1
                )
            # rotation 进 AuditLog（按 tenant 分组，规则 24）
            for tenant_id, count in rotated_by_tenant.items():
                await insert_audit(
                    conn,
                    AuditRecord(
                        audit_id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        request_id=request_id,
                        action="secret.rotate_master_key",
                        target_type="secret_key",
                        target_id=new_key_id,
                        trace_id=trace_id,
                        before={"key_id": old_key_id},
                        after={"key_id": new_key_id, "reencrypted": count},
                    ),
                )
        return sum(rotated_by_tenant.values())

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
