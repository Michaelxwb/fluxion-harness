from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AgentDefinition, ModelDefinition


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        enabled: bool | None,
        last_test_status: str | None,
    ) -> tuple[list[ModelDefinition], int]:
        conditions = [ModelDefinition.tenant_id == tenant_id, ModelDefinition.is_deleted.is_(False)]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    ModelDefinition.key.ilike(pattern),
                    ModelDefinition.name.ilike(pattern),
                    ModelDefinition.model_id.ilike(pattern),
                )
            )
        if enabled is not None:
            conditions.append(ModelDefinition.enabled.is_(enabled))
        if last_test_status:
            conditions.append(ModelDefinition.last_test_status == last_test_status)
        total = await self._session.scalar(
            select(func.count()).select_from(ModelDefinition).where(*conditions)
        )
        rows = await self._session.scalars(
            select(ModelDefinition)
            .where(*conditions)
            .order_by(ModelDefinition.update_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def get(self, tenant_id: str, model_id: uuid.UUID) -> ModelDefinition | None:
        model: ModelDefinition | None = await self._session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.id == model_id,
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return model

    async def find_by_key(self, tenant_id: str, key: str) -> ModelDefinition | None:
        model: ModelDefinition | None = await self._session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.key == key,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return model

    async def add(self, model: ModelDefinition) -> ModelDefinition:
        self._session.add(model)
        await self._session.flush()
        return model

    async def count_agent_references(self, model_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentDefinition)
            .where(AgentDefinition.model_id == model_id, AgentDefinition.is_deleted.is_(False))
        )
        return int(total or 0)

    async def list_by_ids(self, tenant_id: str, model_ids: Sequence[uuid.UUID]) -> Sequence[ModelDefinition]:
        rows = await self._session.scalars(
            select(ModelDefinition).where(
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.id.in_(model_ids),
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return list(rows)
