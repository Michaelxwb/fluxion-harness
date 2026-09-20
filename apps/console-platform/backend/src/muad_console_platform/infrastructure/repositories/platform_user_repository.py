import uuid

from sqlalchemy import func, or_, select
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

    async def get_for_update(self, tenant_id: str, user_id: uuid.UUID) -> PlatformUser | None:
        user: PlatformUser | None = await self._session.scalar(
            select(PlatformUser)
            .where(
                PlatformUser.id == user_id,
                PlatformUser.tenant_id == tenant_id,
                PlatformUser.is_deleted.is_(False),
            )
            .with_for_update()
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

    async def list(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None = None,
        status: str | None = None,
    ) -> tuple[list[PlatformUser], int]:
        conditions = [PlatformUser.tenant_id == tenant_id, PlatformUser.is_deleted.is_(False)]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    PlatformUser.user_code.ilike(pattern),
                    PlatformUser.display_name.ilike(pattern),
                )
            )
        if status:
            conditions.append(PlatformUser.status == status)
        total = await self._session.scalar(select(func.count()).select_from(PlatformUser).where(*conditions))
        rows = await self._session.scalars(
            select(PlatformUser)
            .where(*conditions)
            .order_by(PlatformUser.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def find_by_code(self, tenant_id: str, user_code: str) -> PlatformUser | None:
        user: PlatformUser | None = await self._session.scalar(
            select(PlatformUser).where(
                PlatformUser.tenant_id == tenant_id,
                PlatformUser.user_code == user_code,
                PlatformUser.is_deleted.is_(False),
            )
        )
        return user

    async def add(self, user: PlatformUser) -> PlatformUser:
        self._session.add(user)
        await self._session.flush()
        return user
