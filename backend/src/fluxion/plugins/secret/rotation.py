"""Master key rotation 批量重加密（§16.3；rule 17 密钥材料不落库）。

单事务原子完成：新 key 登记（key_id 冲突拒绝）→ 旧 key revoke（首次补登记）→
全部旧密文解旧密→新密加密 → 按 tenant 分组写 AuditLog（rule 24）。返回重加密记录数。

调用方（PostgresEncryptedSecretStore.rotate_master_key）持有 keyring 内存态，
在事务成功提交后收口（revoke 旧 key、切换 active key_id）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from sqlalchemy import insert, select, update

from fluxion.plugins.secret.crypto import (
    CIPHER_VERSION,
    decrypt_secret,
    encrypt_secret,
)
from fluxion.registry.publish_sqlalchemy import insert_audit
from fluxion.registry.schema import secret_credentials, secret_master_keys
from fluxion.registry.store import AuditRecord
from fluxion.runtime.secrets import SecretProviderError


async def rotate_master_key_batch(
    *,
    engine: Any,
    old_key_id: str,
    new_key_id: str,
    old_key: bytes,
    new_key: bytes,
    actor_id: str,
    request_id: str,
    trace_id: str | None,
    now: datetime,
) -> int:
    """单事务重加密：新 key 登记 + 旧 key revoke + 全部旧密文重加密 + AuditLog。"""
    async with engine.begin() as conn:
        # 注册表（review P1-1）：新 key 登记 + 旧 key revoke 与重加密同事务——
        # active key_id 事实源原子切换；key_id 已注册 → 拒绝（并发双旋转守护）。
        existing = (
            await conn.execute(
                select(secret_master_keys.c.key_id).where(
                    secret_master_keys.c.key_id == new_key_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise SecretProviderError(
                "secret_key_conflict", f"key_id {new_key_id} already registered"
            )
        await conn.execute(
            insert(secret_master_keys).values(
                key_id=new_key_id, created_at=now, revoked_at=None
            )
        )
        registered_old = (
            await conn.execute(
                select(secret_master_keys.c.key_id).where(
                    secret_master_keys.c.key_id == old_key_id
                )
            )
        ).scalar_one_or_none()
        if registered_old is None:
            # 首次旋转（旧 key 未登记过）：补登记并立即 revoke，注册表完整
            await conn.execute(
                insert(secret_master_keys).values(
                    key_id=old_key_id, created_at=now, revoked_at=now
                )
            )
        else:
            await conn.execute(
                update(secret_master_keys)
                .where(secret_master_keys.c.key_id == old_key_id)
                .values(revoked_at=now)
            )
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
                plaintext = decrypt_secret(
                    old_key, record.ref, record.nonce, record.ciphertext
                )
            except InvalidTag as exc:
                raise SecretProviderError(
                    "secret_decrypt_failed", f"{record.ref} decrypt failed"
                ) from exc
            nonce, ciphertext = encrypt_secret(new_key, record.ref, plaintext)
            await conn.execute(
                update(secret_credentials)
                .where(secret_credentials.c.ref == record.ref)
                .values(
                    nonce=nonce,
                    ciphertext=ciphertext,
                    key_id=new_key_id,
                    cipher_version=CIPHER_VERSION,
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


__all__ = ["rotate_master_key_batch"]
