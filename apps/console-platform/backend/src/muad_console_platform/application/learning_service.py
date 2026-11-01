"""Console 经验管理读服务：租户作用域 + 契约投影。"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError, ErrorCode
from muad_contracts.learning_admin import ExperienceDetail, ExperienceItem, FamilyDetail, FamilyItem
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.learning_repository import LearningRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository


class LearningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._learning = LearningRepository(session)
        self._users = PlatformUserRepository(session)

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)

    async def list_experiences(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        state: str | None,
        family: str | None,
        category: str | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ExperienceItem], dict[str, int]]:
        await self._require_user(tenant_id, user_id)
        items, _, counts = await self._learning.list_experiences(
            tenant_id,
            user_id,
            state=state,
            family=family,
            category=category,
            query=query,
            page=page,
            page_size=page_size,
        )
        return [ExperienceItem.model_validate(item) for item in items], counts

    async def get_experience(
        self, tenant_id: str, user_id: uuid.UUID, key: str
    ) -> ExperienceDetail:
        await self._require_user(tenant_id, user_id)
        item = await self._learning.get_experience(tenant_id, user_id, key)
        if item is None:
            raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)
        return ExperienceDetail.model_validate(item)

    async def list_families(
        self,
        tenant_id: str,
        *,
        source: str | None,
        query: str | None,
        window_days: int,
        page: int,
        page_size: int,
    ) -> tuple[list[FamilyItem], int, int]:
        items, total, unclassified = await self._learning.list_families(
            tenant_id,
            source=source,
            query=query,
            window_days=window_days,
            page=page,
            page_size=page_size,
        )
        return [FamilyItem.model_validate(item) for item in items], total, unclassified

    async def get_family(self, tenant_id: str, family: str, *, window_days: int) -> FamilyDetail:
        data: dict[str, Any] = await self._learning.get_family(
            tenant_id, family, window_days=window_days
        )
        return FamilyDetail.model_validate(data)
