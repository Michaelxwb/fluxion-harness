from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SecretRecord:
    ref: str
    tenant_id: str
    name: str
    version: str
    nonce: bytes
    ciphertext: bytes
    revoked: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    ref: str
    tenant_id: str
    provider: str
    version: str
    revoked: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    value: str
    version: str


class SecretStore(Protocol):
    async def resolve(self, ref: str) -> ResolvedCredential: ...


class SecretMetadataStore(Protocol):
    async def list_metadata(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[SecretMetadata], int]: ...


class LocalEncryptedSecretStore:
    def __init__(self, *, master_key: bytes | None) -> None:
        if master_key is None:
            raise SecretProviderError("secret_master_key_missing", "master key is required")
        if len(master_key) != 32:
            raise SecretProviderError("secret_master_key_invalid", "AES-256-GCM key must be 32 bytes")
        self._master_key = master_key
        self._records: dict[str, SecretRecord] = {}

    @classmethod
    def from_env(cls, env_name: str = "FLUXION_SECRET_MASTER_KEY") -> LocalEncryptedSecretStore:
        raw = os.environ.get(env_name)
        if raw is None:
            raise SecretProviderError("secret_master_key_missing", f"{env_name} is required")
        try:
            key = base64.b64decode(raw)
        except ValueError as exc:
            raise SecretProviderError("secret_master_key_invalid", "master key must be base64") from exc
        return cls(master_key=key)

    async def put(self, tenant_id: str, name: str, plaintext: str) -> str:
        return self._put_version(tenant_id, name, "1", plaintext)

    async def rotate(self, ref: str, plaintext: str) -> str:
        current = self._record(ref)
        version = str(int(current.version) + 1)
        return self._put_version(current.tenant_id, current.name, version, plaintext)

    async def revoke(self, ref: str) -> None:
        current = self._record(ref)
        self._records[ref] = replace(current, revoked=True)

    async def resolve(self, ref: str) -> ResolvedCredential:
        record = self._record(ref)
        if record.revoked:
            raise SecretProviderError("secret_revoked", f"{ref} is revoked")
        try:
            plaintext = AESGCM(self._master_key).decrypt(record.nonce, record.ciphertext, ref.encode())
        except InvalidTag as exc:
            raise SecretProviderError("secret_decrypt_failed", f"{ref} decrypt failed") from exc
        return ResolvedCredential(value=plaintext.decode("utf-8"), version=record.version)

    async def list_metadata(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[SecretMetadata], int]:
        records = sorted(
            (record for record in self._records.values() if record.tenant_id == tenant_id),
            key=lambda record: record.created_at,
            reverse=True,
        )
        page = records[offset : offset + limit]
        return [
            SecretMetadata(
                ref=record.ref,
                tenant_id=record.tenant_id,
                provider="local_encrypted",
                version=record.version,
                revoked=record.revoked,
                created_at=record.created_at,
            )
            for record in page
        ], len(records)

    def export_encrypted_records(self) -> dict[str, SecretRecord]:
        return dict(self._records)

    def import_encrypted_records(self, records: dict[str, SecretRecord]) -> None:
        self._records = dict(records)

    def _put_version(self, tenant_id: str, name: str, version: str, plaintext: str) -> str:
        ref = f"secret://{tenant_id}/{name}@{version}"
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._master_key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            ref.encode(),
        )
        self._records[ref] = SecretRecord(
            ref=ref,
            tenant_id=tenant_id,
            name=name,
            version=version,
            nonce=nonce,
            ciphertext=ciphertext,
        )
        return ref

    def _record(self, ref: str) -> SecretRecord:
        record = self._records.get(ref)
        if record is None:
            raise SecretProviderError("secret_not_found", f"{ref} not found")
        return record


class CredentialResolver:
    def __init__(self, store: SecretStore) -> None:
        self._store = store

    async def resolve(self, ref: str, *, tenant_id: str) -> str:
        return (await self.resolve_with_metadata(ref, tenant_id=tenant_id)).value

    async def resolve_with_metadata(
        self, ref: str, *, tenant_id: str
    ) -> ResolvedCredential:
        # 跨租户 IDOR 收口：credential_ref 内嵌租户必须与调用方租户一致。
        # 此前 resolve 只按 ref 精确查表，tenant A 把 binding 的 credential_ref
        # 填成 secret://tenant-b/... 即可解出 tenant B 的凭据调用外部系统。
        ref_tenant = secret_ref_tenant(ref)
        if ref_tenant is not None and ref_tenant != tenant_id:
            raise SecretProviderError(
                "secret_tenant_mismatch",
                f"{ref} does not belong to tenant {tenant_id}",
            )
        return await self._store.resolve(ref)


def secret_ref_tenant(ref: str) -> str | None:
    """从 secret://{tenant_id}/{name}@{version} 解析租户段。

    name 可含 `/`；非 secret:// 协议的 ref（如外部 vault）返回 None，
    交由具体 store 自行鉴权。
    """
    if not ref.startswith("secret://"):
        return None
    rest = ref[len("secret://"):]
    slash = rest.find("/")
    if slash <= 0:
        return None
    return rest[:slash]
