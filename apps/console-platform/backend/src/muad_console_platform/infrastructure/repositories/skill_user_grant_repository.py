import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import PlatformUser, SkillUserGrant


class SkillUserGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, skill_id: uuid.UUID, user_id: uuid.UUID) -> SkillUserGrant | None:
        grant: SkillUserGrant | None = await self._session.scalar(
            select(SkillUserGrant).where(
                SkillUserGrant.skill_id == skill_id,
                SkillUserGrant.user_id == user_id,
            )
        )
        return grant

    async def list_with_users(
        self,
        skill_id: uuid.UUID,
    ) -> list[tuple[SkillUserGrant, PlatformUser]]:
        result = await self._session.execute(
            select(SkillUserGrant, PlatformUser)
            .join(PlatformUser, PlatformUser.id == SkillUserGrant.user_id)
            .where(
                SkillUserGrant.skill_id == skill_id,
                SkillUserGrant.is_deleted.is_(False),
                PlatformUser.is_deleted.is_(False),
            )
            .order_by(SkillUserGrant.create_time.desc())
        )
        return [(grant, user) for grant, user in result.all()]

    async def count_active(self, skill_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(SkillUserGrant)
            .where(
                SkillUserGrant.skill_id == skill_id,
                SkillUserGrant.is_deleted.is_(False),
            )
        )
        return int(total or 0)

    async def add(self, grant: SkillUserGrant) -> SkillUserGrant:
        self._session.add(grant)
        await self._session.flush()
        return grant
