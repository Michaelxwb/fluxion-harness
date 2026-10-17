"""共享账号额度组服务（设计 §3.3、§3.4 API-02）。

- `rpm`/`tpm` 为 `None` 表示该维度不设应用内限流（不是零额度）；限流值按真实供方额度填写；
- 创建支持 `Idempotency-Key`（RULE-api-002）：同键同指纹重放首次结果、异指纹
  `IDEMPOTENCY_MISMATCH`；指纹为**规范化 JSON**（`sort_keys` + 紧凑分隔符）的 SHA256，
  判别键含 `endpoint`/`tenant_id`/`actor`；并发由 `(tenant_id, key, endpoint)` 事务
  advisory 锁串行（`control.skill_import_idempotency` 兜底）；
- 降额/新开限流维度先排空（§3.6）：`runtime.model_call_lease` 仍有该组的
  `ACTIVE`/`UNKNOWN` 行时，改小任一限流或从「不设限」新开限流是显式冲突，不静默丢弃占用；
- 删除是软删；仍被活跃凭据引用时显式冲突（设计 §3.4「删除被引用项明确冲突」）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.model_pool import ModelQuotaGroupInput, ModelQuotaGroupUpdate
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelQuotaGroup, SkillImportIdempotency
from ..infrastructure.repositories.model_quota_group_repository import ModelQuotaGroupRepository
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "MODEL_QUOTA_GROUP"
#: 幂等写入口标识（`control.skill_import_idempotency.endpoint`，≤32 字符）
CREATE_ENDPOINT = "model-quota-group-create"
#: 去重语义由数据库 partial unique 兜底；并发落败者按该约束名归一到同一冲突码
DUPLICATE_CONSTRAINTS = frozenset({"uq_model_quota_group_tenant_key"})


def quota_group_snapshot(group: ModelQuotaGroup) -> dict[str, Any]:
    """审计快照：只列额度组自身的容量声明，不含任何密钥/连接字段。"""
    return {
        "key": group.key,
        "max_inflight": group.max_inflight,
        "rpm": group.rpm,
        "tpm": group.tpm,
        "revision": group.revision,
    }


def _is_duplicate_violation(exc: IntegrityError) -> bool:
    detail = str(exc.orig)
    return any(name in detail for name in DUPLICATE_CONSTRAINTS)


def create_fingerprint(tenant_id: str, actor: AuditActor, payload: ModelQuotaGroupInput) -> str:
    """创建请求的规范化 JSON 指纹（RULE-api-002）。"""
    canonical = json.dumps(
        {
            "endpoint": CREATE_ENDPOINT,
            "tenant_id": tenant_id,
            "actor": str(actor.account_id),
            "key": payload.key,
            "max_inflight": payload.max_inflight,
            "rpm": payload.rpm,
            "tpm": payload.tpm,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tightens_limit(current: int | None, proposed: int | None) -> bool:
    """从「不设限」新开限流或把数值调小都算降额；放开限流/调大/不变都不算。"""
    if proposed is None:
        return False
    if current is None:
        return True
    return proposed < current


def requires_drain(group: ModelQuotaGroup, payload: ModelQuotaGroupInput) -> bool:
    """改小 max_inflight 或任一限流维度 = 降额，旧在飞调用必须先排空（§3.6）。"""
    return (
        payload.max_inflight < group.max_inflight
        or _tightens_limit(group.rpm, payload.rpm)
        or _tightens_limit(group.tpm, payload.tpm)
    )


class ModelQuotaGroupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._groups = ModelQuotaGroupRepository(session)
        self._audit = AuditService(session)

    async def list_groups(
        self, tenant_id: str, page: int, page_size: int
    ) -> tuple[list[ModelQuotaGroup], int]:
        return await self._groups.list(tenant_id, page, page_size)

    async def get_group(self, tenant_id: str, group_id: uuid.UUID) -> ModelQuotaGroup:
        group = await self._groups.get(tenant_id, group_id)
        if group is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return group

    async def create_group(
        self,
        tenant_id: str,
        payload: ModelQuotaGroupInput,
        actor: AuditActor,
        idempotency_key: str | None = None,
    ) -> ModelQuotaGroup:
        """登记共享账号额度组；带 `Idempotency-Key` 时同键同指纹重放首次结果。"""
        fingerprint = create_fingerprint(tenant_id, actor, payload)
        if idempotency_key:
            await self._lock_idempotency(tenant_id, idempotency_key)
            replayed = await self._replay_create(tenant_id, idempotency_key, fingerprint)
            if replayed is not None:
                return await self.get_group(tenant_id, uuid.UUID(str(replayed["id"])))
        existing = await self._groups.find_by_key(tenant_id, payload.key)
        if existing is not None:
            raise AppError(ErrorCode.MODEL_QUOTA_GROUP_EXISTS)
        group = ModelQuotaGroup(
            tenant_id=tenant_id,
            key=payload.key,
            rpm=payload.rpm,
            tpm=payload.tpm,
            max_inflight=payload.max_inflight,
        )
        try:
            created = await self._groups.add(group)
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_QUOTA_GROUP_EXISTS) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created, None, created)
        if idempotency_key:
            await self._record_create_receipt(tenant_id, idempotency_key, fingerprint, created)
        return created

    async def update_group(
        self,
        tenant_id: str,
        group_id: uuid.UUID,
        payload: ModelQuotaGroupUpdate,
        actor: AuditActor,
    ) -> ModelQuotaGroup:
        """整份替换可编辑字段 + revision CAS；降额前必须已排空在飞调用。"""
        group = await self.get_group(tenant_id, group_id)
        before = quota_group_snapshot(group)
        duplicate = await self._groups.find_by_key(tenant_id, payload.key)
        if duplicate is not None and duplicate.id != group_id:
            raise AppError(ErrorCode.MODEL_QUOTA_GROUP_EXISTS)
        if requires_drain(group, payload) and await self._groups.has_live_leases(tenant_id, group_id):
            raise AppError(ErrorCode.MODEL_CAPACITY_DRAIN_REQUIRED)
        values: dict[str, Any] = {
            "key": payload.key,
            "max_inflight": payload.max_inflight,
            "rpm": payload.rpm,
            "tpm": payload.tpm,
        }
        try:
            updated = await self._groups.cas_update(tenant_id, group_id, payload.expected_revision, values)
        except IntegrityError as exc:
            if not _is_duplicate_violation(exc):
                raise
            raise AppError(ErrorCode.MODEL_QUOTA_GROUP_EXISTS) from exc
        if not updated:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.refresh(group)
        await self._record_audit(tenant_id, actor, "UPDATE", group, before, group)
        return group

    async def delete_group(self, tenant_id: str, group_id: uuid.UUID, actor: AuditActor) -> None:
        group = await self.get_group(tenant_id, group_id)
        references = await self._groups.count_credential_references(tenant_id, group_id)
        if references > 0:
            raise AppError(ErrorCode.MODEL_QUOTA_GROUP_IN_USE)
        before = quota_group_snapshot(group)
        group.is_deleted = True
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", group, before, None)

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
        group: ModelQuotaGroup,
    ) -> None:
        """回执只存行 ID，重放按 ID 回读当时行。"""
        self._session.add(
            SkillImportIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=CREATE_ENDPOINT,
                request_fingerprint=fingerprint,
                response_json={"id": str(group.id)},
            )
        )
        await self._session.flush()

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        group: ModelQuotaGroup,
        before: dict[str, Any] | None,
        after: ModelQuotaGroup | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=group.id,
            action=action,
            before=before,
            after=quota_group_snapshot(after) if after is not None else None,
        )
