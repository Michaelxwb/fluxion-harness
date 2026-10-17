"""模型物理凭据仓储（设计 §3.3 `control.model_credential`）。

去重与并发由真实 partial unique 兜底（`uq_model_credential_tenant_key` /
`uq_model_credential_tenant_provider_digest`）；本层只做带租户谓词的读写，
不提交事务（提交/回滚由调用方决定）。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ModelCredential, ModelDeployment, ModelQuotaGroup


class ModelCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, tenant_id: str, page: int, page_size: int) -> tuple[list[ModelCredential], int]:
        conditions = [
            ModelCredential.tenant_id == tenant_id,
            ModelCredential.is_deleted.is_(False),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(ModelCredential).where(*conditions)
        )
        rows = await self._session.scalars(
            select(ModelCredential)
            .where(*conditions)
            .order_by(ModelCredential.update_time.desc(), ModelCredential.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def get(self, tenant_id: str, credential_id: uuid.UUID) -> ModelCredential | None:
        credential: ModelCredential | None = await self._session.scalar(
            select(ModelCredential).where(
                ModelCredential.id == credential_id,
                ModelCredential.tenant_id == tenant_id,
                ModelCredential.is_deleted.is_(False),
            )
        )
        return credential

    async def find_by_key(self, tenant_id: str, key: str) -> ModelCredential | None:
        credential: ModelCredential | None = await self._session.scalar(
            select(ModelCredential).where(
                ModelCredential.tenant_id == tenant_id,
                ModelCredential.key == key,
                ModelCredential.is_deleted.is_(False),
            )
        )
        return credential

    async def find_by_provider_digest(
        self, tenant_id: str, provider_key: str, api_key_digest: str
    ) -> ModelCredential | None:
        credential: ModelCredential | None = await self._session.scalar(
            select(ModelCredential).where(
                ModelCredential.tenant_id == tenant_id,
                ModelCredential.provider_key == provider_key,
                ModelCredential.api_key_digest == api_key_digest,
                ModelCredential.is_deleted.is_(False),
            )
        )
        return credential

    async def find_quota_group(self, tenant_id: str, quota_group_id: uuid.UUID) -> ModelQuotaGroup | None:
        """凭据 `(tenant_id, quota_group_id)` 复合 FK 的目标行：不存在/跨租户都不写。"""
        group: ModelQuotaGroup | None = await self._session.scalar(
            select(ModelQuotaGroup).where(
                ModelQuotaGroup.id == quota_group_id,
                ModelQuotaGroup.tenant_id == tenant_id,
                ModelQuotaGroup.is_deleted.is_(False),
            )
        )
        return group

    async def add(self, credential: ModelCredential) -> ModelCredential:
        self._session.add(credential)
        await self._session.flush()
        return credential

    async def cas_update(
        self,
        tenant_id: str,
        credential_id: uuid.UUID,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """条件更新做 CAS：只有 revision 仍等于 expected_revision 时才写入。

        返回是否命中（False = 期间被他人改过，调用方应抛 REVISION_CONFLICT）。
        """
        result = await self._session.execute(
            update(ModelCredential)
            .where(
                ModelCredential.id == credential_id,
                ModelCredential.tenant_id == tenant_id,
                ModelCredential.revision == expected_revision,
                ModelCredential.is_deleted.is_(False),
            )
            .values(**values, revision=expected_revision + 1, update_time=func.now())
        )
        return cast(CursorResult[Any], result).rowcount == 1

    async def count_deployment_references(self, tenant_id: str, credential_id: uuid.UUID) -> int:
        """仍引用该凭据的活跃模型成员数（删除冲突判定）。"""
        total = await self._session.scalar(
            select(func.count())
            .select_from(ModelDeployment)
            .where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.credential_id == credential_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return int(total or 0)
