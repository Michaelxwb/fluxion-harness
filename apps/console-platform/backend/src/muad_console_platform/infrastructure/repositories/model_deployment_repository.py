"""模型成员关系仓储（设计 §3.3 `control.model_deployment`）。

成员只引用 `credential_id`，**不复制任何密钥/连接字段**；同模型内成员名与凭据各自唯一，
由真实 partial unique 兜底（`uq_model_deployment_tenant_model_key` /
`uq_model_deployment_tenant_model_credential`）；本层只做带租户/模型谓词的读写，
不提交事务（提交/回滚由调用方决定）。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ModelDeployment


class ModelDeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_model(
        self, tenant_id: str, model_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[ModelDeployment], int]:
        conditions = [
            ModelDeployment.tenant_id == tenant_id,
            ModelDeployment.model_id == model_id,
            ModelDeployment.is_deleted.is_(False),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(ModelDeployment).where(*conditions)
        )
        rows = await self._session.scalars(
            select(ModelDeployment)
            .where(*conditions)
            .order_by(ModelDeployment.create_time, ModelDeployment.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def get(
        self, tenant_id: str, model_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.id == deployment_id,
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def find_by_key(self, tenant_id: str, model_id: uuid.UUID, key: str) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.key == key,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def find_by_credential(
        self, tenant_id: str, model_id: uuid.UUID, credential_id: uuid.UUID
    ) -> ModelDeployment | None:
        deployment: ModelDeployment | None = await self._session.scalar(
            select(ModelDeployment).where(
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.credential_id == credential_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
        return deployment

    async def add(self, deployment: ModelDeployment) -> ModelDeployment:
        self._session.add(deployment)
        await self._session.flush()
        return deployment

    async def cas_update(
        self,
        tenant_id: str,
        model_id: uuid.UUID,
        deployment_id: uuid.UUID,
        expected_revision: int,
        values: dict[str, Any],
    ) -> bool:
        """条件更新做 CAS：只有 revision 仍等于 expected_revision 时才写入。

        返回是否命中（False = 期间被他人改过，调用方应抛 REVISION_CONFLICT）。
        """
        result = await self._session.execute(
            update(ModelDeployment)
            .where(
                ModelDeployment.id == deployment_id,
                ModelDeployment.tenant_id == tenant_id,
                ModelDeployment.model_id == model_id,
                ModelDeployment.revision == expected_revision,
                ModelDeployment.is_deleted.is_(False),
            )
            .values(**values, revision=expected_revision + 1, update_time=func.now())
        )
        return cast(CursorResult[Any], result).rowcount == 1
