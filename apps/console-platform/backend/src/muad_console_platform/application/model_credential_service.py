"""物理模型凭据服务（设计 §3.3、§3.4 API-16）。

- 去重身份 = `(tenant_id, provider_key, api_key_digest)`：同一把物理 Key 只登记一行，
  重录在弹回冲突前不会重建行、不会清零既有计量；
- 计量身份 `key_resource_id` 由 tenant/provider/Key 指纹派生（ADR-08）：删除重录复用同一
  身份（窗口不重置），轮换出新 Key 值即换新身份（旧在飞/UNKNOWN 留在旧身份，不迁移）；
- 明文只落 Owner 表：审计快照按 MCP 先例剔除密钥与指纹，不进入读取投影/日志；
- 创建支持 `Idempotency-Key`（RULE-api-002）：同键同指纹重放首次结果、异指纹
  `IDEMPOTENCY_MISMATCH`；指纹为**规范化 JSON**（`sort_keys` + 紧凑分隔符）的 SHA256，
  判别键含 `endpoint`/`tenant_id`/`actor`，密钥只以摘要入指纹。并发由
  `(tenant_id, key, endpoint)` 事务 advisory 锁串行（`control.skill_import_idempotency` 兜底）；
- 降额/换归属先排空（§3.6、§3.7 `max_inflight` 行）：`runtime.model_call_lease` 仍有
  `ACTIVE`/`UNKNOWN` 行时，改小容量或改 provider/endpoint/额度组是显式冲突，不静默丢弃占用。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.model_pool import ModelCredentialCreate, ModelCredentialUpdate
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelCredential, SkillImportIdempotency
from ..infrastructure.repositories.model_credential_repository import ModelCredentialRepository
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "MODEL_CREDENTIAL"
#: 幂等写入口标识（`control.skill_import_idempotency.endpoint`，≤32 字符）
CREATE_ENDPOINT = "model-credential-create"
#: 去重语义由数据库 partial unique 兜底；并发落败者按这些约束名归一到同一冲突码
DUPLICATE_CONSTRAINTS = frozenset(
    {"uq_model_credential_tenant_key", "uq_model_credential_tenant_provider_digest"}
)


def compute_api_key_digest(api_key: str) -> str:
    """明文密钥的 SHA-256 小写十六进制摘要（仅用于去重，与列 CHECK 同口径）。"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def derive_key_resource_id(tenant_id: str, provider_key: str, api_key_digest: str) -> uuid.UUID:
    """由 tenant/provider/Key 指纹派生稳定 `key_resource_id`（ADR-08；不落库、不随行 ID 变）。

    同一物理 Key 在多模型/Agent 上共享同一计量身份；轮换出新 Key 值则换新身份，
    旧身份上的在飞/UNKNOWN 占用不被迁移，删除重录回到同一身份。
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"muad:model-key:{tenant_id}:{provider_key}:{api_key_digest}")


def credential_snapshot(credential: ModelCredential) -> dict[str, Any]:
    """审计快照只列非密钥字段：明文与指纹都不进入（RULE-secret-001）。"""
    return {
        "key": credential.key,
        "provider_key": credential.provider_key,
        "base_url": credential.base_url,
        "quota_group_id": str(credential.quota_group_id) if credential.quota_group_id else None,
        "max_inflight": credential.max_inflight,
        "enabled": credential.enabled,
        "revision": credential.revision,
    }


def _is_duplicate_violation(exc: IntegrityError) -> bool:
    detail = str(exc.orig)
    return any(name in detail for name in DUPLICATE_CONSTRAINTS)


def create_fingerprint(
    tenant_id: str,
    actor: AuditActor,
    payload: ModelCredentialCreate,
    api_key_digest: str,
) -> str:
    """创建请求的规范化 JSON 指纹；密钥只以 SHA-256 摘要进入（RULE-api-002）。"""
    canonical = json.dumps(
        {
            "endpoint": CREATE_ENDPOINT,
            "tenant_id": tenant_id,
            "actor": str(actor.account_id),
            "key": payload.key,
            "provider_key": payload.provider_key,
            "base_url": payload.base_url,
            "quota_group_id": str(payload.quota_group_id) if payload.quota_group_id else None,
            "max_inflight": payload.max_inflight,
            "enabled": payload.enabled,
            "api_key_checksum": api_key_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def requires_drain(
    credential: ModelCredential, payload: ModelCredentialCreate | ModelCredentialUpdate
) -> bool:
    """改小容量或换 provider/endpoint/额度归属 = 连接配置变更，旧在飞调用必须先排空。

    纯值轮换（同目标换密钥）与扩容不回收在飞调用，可实时生效（§3.6）。
    """
    return (
        payload.max_inflight < credential.max_inflight
        or payload.quota_group_id != credential.quota_group_id
        or payload.provider_key != credential.provider_key
        or payload.base_url != credential.base_url
    )


class ModelCredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._credentials = ModelCredentialRepository(session)
        self._audit = AuditService(session)

    async def list_credentials(
        self, tenant_id: str, page: int, page_size: int
    ) -> tuple[list[ModelCredential], int]:
        return await self._credentials.list(tenant_id, page, page_size)

    async def get_credential(self, tenant_id: str, credential_id: uuid.UUID) -> ModelCredential:
        credential = await self._credentials.get(tenant_id, credential_id)
        if credential is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return credential

    async def create_credential(
        self,
        tenant_id: str,
        payload: ModelCredentialCreate,
        actor: AuditActor,
        idempotency_key: str | None = None,
    ) -> ModelCredential:
        """登记物理 Key；带 `Idempotency-Key` 时同键同指纹重放首次结果（RULE-api-002）。"""
        secret = payload.api_key.get_secret_value()
        digest = compute_api_key_digest(secret)
        fingerprint = create_fingerprint(tenant_id, actor, payload, digest)
        if idempotency_key:
            await self._lock_idempotency(tenant_id, idempotency_key)
            replayed = await self._replay_create(tenant_id, idempotency_key, fingerprint)
            if replayed is not None:
                return await self.get_credential(tenant_id, uuid.UUID(str(replayed["id"])))
        await self._assert_identity_free(tenant_id, payload.key, payload.provider_key, digest)
        await self._assert_quota_group(tenant_id, payload.quota_group_id)
        credential = ModelCredential(
            tenant_id=tenant_id,
            key=payload.key,
            provider_key=payload.provider_key,
            base_url=payload.base_url,
            api_key=secret,
            api_key_digest=digest,
            quota_group_id=payload.quota_group_id,
            max_inflight=payload.max_inflight,
            enabled=payload.enabled,
        )
        try:
            created = await self._credentials.add(credential)
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_CREDENTIAL_EXISTS) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created, None, created)
        if idempotency_key:
            await self._record_create_receipt(tenant_id, idempotency_key, fingerprint, created)
        return created

    async def update_credential(
        self,
        tenant_id: str,
        credential_id: uuid.UUID,
        payload: ModelCredentialCreate | ModelCredentialUpdate,
        expected_revision: int,
        actor: AuditActor,
    ) -> ModelCredential:
        """整份替换可编辑字段：值轮换更新本行（行/成员身份不变），新值换新计量身份。

        缺省 `api_key` 表示保留原值（密钥仅写入，留空保留原值——空串在契约层即 422）。
        """
        credential = await self.get_credential(tenant_id, credential_id)
        before = credential_snapshot(credential)
        secret = payload.api_key.get_secret_value() if payload.api_key is not None else None
        digest = compute_api_key_digest(secret) if secret is not None else credential.api_key_digest
        await self._assert_identity_free(
            tenant_id, payload.key, payload.provider_key, digest, exclude_id=credential_id
        )
        await self._assert_quota_group(tenant_id, payload.quota_group_id)
        if requires_drain(credential, payload) and await self._credentials.has_live_leases(
            tenant_id, credential_id
        ):
            raise AppError(ErrorCode.MODEL_CAPACITY_DRAIN_REQUIRED)
        values: dict[str, Any] = {
            "key": payload.key,
            "provider_key": payload.provider_key,
            "base_url": payload.base_url,
            "quota_group_id": payload.quota_group_id,
            "max_inflight": payload.max_inflight,
            "enabled": payload.enabled,
        }
        if secret is not None:
            values["api_key"] = secret
            values["api_key_digest"] = digest
        try:
            updated = await self._credentials.cas_update(tenant_id, credential_id, expected_revision, values)
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_CREDENTIAL_EXISTS) from exc
        if not updated:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.refresh(credential)
        await self._record_audit(tenant_id, actor, "UPDATE", credential, before, credential)
        return credential

    async def delete_credential(
        self,
        tenant_id: str,
        credential_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        credential = await self.get_credential(tenant_id, credential_id)
        references = await self._credentials.count_deployment_references(tenant_id, credential_id)
        if references > 0:
            raise AppError(ErrorCode.MODEL_CREDENTIAL_IN_USE)
        before = credential_snapshot(credential)
        credential.is_deleted = True
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", credential, before, None)

    async def _assert_identity_free(
        self,
        tenant_id: str,
        key: str,
        provider_key: str,
        api_key_digest: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        """key 与 (provider, 摘要) 去重；`exclude_id` 让轮换重提同值时与自身比对不算重复。"""
        by_key = await self._credentials.find_by_key(tenant_id, key)
        if by_key is not None and by_key.id != exclude_id:
            raise AppError(ErrorCode.MODEL_CREDENTIAL_EXISTS)
        by_digest = await self._credentials.find_by_provider_digest(tenant_id, provider_key, api_key_digest)
        if by_digest is not None and by_digest.id != exclude_id:
            raise AppError(ErrorCode.MODEL_CREDENTIAL_EXISTS)

    async def _assert_quota_group(self, tenant_id: str, quota_group_id: uuid.UUID | None) -> None:
        """复合 FK 目标必须存在且同租户；跨租户/不存在都按归属缺失处理。

        `None` 合法：无共享账号额度组的 Key 独占自身容量（0021 迁移清单允许该状态）。
        """
        if quota_group_id is None:
            return
        if await self._credentials.find_quota_group(tenant_id, quota_group_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _lock_idempotency(self, tenant_id: str, idempotency_key: str) -> None:
        """同一 (tenant, key, endpoint) 事务内串行：后到者等首个写者提交后再判定重放。"""
        digest = hashlib.sha256(f"{tenant_id}|{idempotency_key}|{CREATE_ENDPOINT}".encode()).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _replay_create(
        self, tenant_id: str, idempotency_key: str, fingerprint: str
    ) -> dict[str, Any] | None:
        record = await self._session.scalar(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == idempotency_key,
                SkillImportIdempotency.endpoint == CREATE_ENDPOINT,
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return dict(record.response_json)

    async def _record_create_receipt(
        self,
        tenant_id: str,
        idempotency_key: str,
        fingerprint: str,
        credential: ModelCredential,
    ) -> None:
        """回执只存行 ID：不存密钥明文/指纹，重放按 ID 回读当时行。"""
        self._session.add(
            SkillImportIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=CREATE_ENDPOINT,
                request_fingerprint=fingerprint,
                response_json={"id": str(credential.id)},
            )
        )
        await self._session.flush()

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        credential: ModelCredential,
        before: dict[str, Any] | None,
        after: ModelCredential | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=credential.id,
            action=action,
            before=before,
            after=credential_snapshot(after) if after is not None else None,
        )
