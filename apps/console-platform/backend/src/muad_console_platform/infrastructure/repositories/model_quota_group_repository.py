"""共享账号额度组仓储（设计 §3.3 `control.model_quota_group`）。

`(tenant_id, key)` 去重与并发由真实 partial unique（`uq_model_quota_group_tenant_key`）
兜底；本层只做带租户谓词的读写，不提交事务（提交/回滚由调用方决定）。

**为什么读 `runtime.model_call_lease`**：降额/新开限流维度的排空判定需要知道该额度组
是否仍有在飞/未确定调用——占用事实由 Runtime 的租约表承载，Console 不拥有该表，只读
同一先例（`audit_query_repository` JOIN `runtime.run_record`、
`artifact_retention_repository`）。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ModelCredential, ModelQuotaGroup

#: 在飞/未确定租约仍占容量（SETTLED 才是终态；过期 ACTIVE 也不能断言远端已停）。
_HAS_LIVE_LEASES_SQL = text(
    "SELECT EXISTS ("
    "SELECT 1 FROM runtime.model_call_lease "
    "WHERE tenant_id = :tenant_id AND quota_group_id = :quota_group_id "
    "AND state IN ('ACTIVE','UNKNOWN') AND is_deleted = false)"
)


class ModelQuotaGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, tenant_id: str, page: int, page_size: int) -> tuple[list[ModelQuotaGroup], int]:
        conditions = [
            ModelQuotaGroup.tenant_id == tenant_id,
            ModelQuotaGroup.is_deleted.is_(False),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(ModelQuotaGroup).where(*conditions)
        )
        rows = await self._session.scalars(
            select(ModelQuotaGroup)
            .where(*conditions)
            .order_by(ModelQuotaGroup.update_time.desc(), ModelQuotaGroup.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def get(self, tenant_id: str, group_id: uuid.UUID) -> ModelQuotaGroup | None:
        group: ModelQuotaGroup | None = await self._session.scalar(
            select(ModelQuotaGroup).where(
                ModelQuotaGroup.id == group_id,
                ModelQuotaGroup.tenant_id == tenant_id,
                ModelQuotaGroup.is_deleted.is_(False),
            )
        )
        return group

    async def find_by_key(self, tenant_id: str, key: str) -> ModelQuotaGroup | None:
        group: ModelQuotaGroup | None = await self._session.scalar(
            select(ModelQuotaGroup).where(
                ModelQuotaGroup.tenant_id == tenant_id,
                ModelQuotaGroup.key == key,
                ModelQuotaGroup.is_deleted.is_(False),
            )
        )
        return group

    async def add(self, group: ModelQuotaGroup) -> ModelQuotaGroup:
        self._session.add(group)
        await self._session.flush()
        return group

    async def cas_update(
        self,
        tenant_id: str,
        group_id: uuid.UUID,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """条件更新做 CAS：只有 revision 仍等于 expected_revision 时才写入。

        返回是否命中（False = 期间被他人改过，调用方应抛 REVISION_CONFLICT）。
        """
        result = await self._session.execute(
            update(ModelQuotaGroup)
            .where(
                ModelQuotaGroup.id == group_id,
                ModelQuotaGroup.tenant_id == tenant_id,
                ModelQuotaGroup.revision == expected_revision,
                ModelQuotaGroup.is_deleted.is_(False),
            )
            .values(**values, revision=expected_revision + 1, update_time=func.now())
        )
        return cast(CursorResult[Any], result).rowcount == 1

    async def count_credential_references(self, tenant_id: str, group_id: uuid.UUID) -> int:
        """仍引用该额度组的活跃模型凭据数（删除冲突判定）。"""
        total = await self._session.scalar(
            select(func.count())
            .select_from(ModelCredential)
            .where(
                ModelCredential.tenant_id == tenant_id,
                ModelCredential.quota_group_id == group_id,
                ModelCredential.is_deleted.is_(False),
            )
        )
        return int(total or 0)

    async def has_live_leases(self, tenant_id: str, group_id: uuid.UUID) -> bool:
        """该额度组是否仍有在飞/未确定租约（降额或新开限流维度的排空判定）。"""
        exists = await self._session.scalar(
            _HAS_LIVE_LEASES_SQL,
            {"tenant_id": tenant_id, "quota_group_id": group_id},
        )
        return bool(exists)
