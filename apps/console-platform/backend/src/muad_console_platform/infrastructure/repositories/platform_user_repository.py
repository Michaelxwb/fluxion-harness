import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import PlatformUser


class PlatformUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: str, user_id: uuid.UUID) -> PlatformUser | None:
        user: PlatformUser | None = await self._session.scalar(
            select(PlatformUser).where(
                PlatformUser.id == user_id,
                PlatformUser.tenant_id == tenant_id,
                PlatformUser.is_deleted.is_(False),
            )
        )
        return user

    async def get_status(self, tenant_id: str, user_id: uuid.UUID) -> str | None:
        status: str | None = await self._session.scalar(
            select(PlatformUser.status).where(
                PlatformUser.id == user_id,
                PlatformUser.tenant_id == tenant_id,
                PlatformUser.is_deleted.is_(False),
            )
        )
        return status
