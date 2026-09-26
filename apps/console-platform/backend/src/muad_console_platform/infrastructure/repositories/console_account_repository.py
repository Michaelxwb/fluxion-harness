import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ConsoleAccount


class ConsoleAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _active_conditions(tenant_id: str) -> tuple[Any, ...]:
        """list 与 count 共用的过滤口径，避免分页 total 与 items 漂移。"""
        return (
            ConsoleAccount.tenant_id == tenant_id,
            ConsoleAccount.is_deleted.is_(False),
        )

    async def find_by_username(self, tenant_id: str, username: str) -> ConsoleAccount | None:
        account: ConsoleAccount | None = await self._session.scalar(
            select(ConsoleAccount).where(
                ConsoleAccount.tenant_id == tenant_id,
                ConsoleAccount.username == username,
                ConsoleAccount.is_deleted.is_(False),
            )
        )
        return account

    async def find_by_username_global(self, username: str) -> list[ConsoleAccount]:
        result = await self._session.scalars(
            select(ConsoleAccount).where(
                ConsoleAccount.username == username,
                ConsoleAccount.is_deleted.is_(False),
            )
        )
        return list(result.all())

    async def get(self, account_id: uuid.UUID) -> ConsoleAccount | None:
        account: ConsoleAccount | None = await self._session.scalar(
            select(ConsoleAccount).where(
                ConsoleAccount.id == account_id,
                ConsoleAccount.is_deleted.is_(False),
            )
        )
        return account

    async def list(self, tenant_id: str, *, limit: int, offset: int) -> list[ConsoleAccount]:
        result = await self._session.scalars(
            select(ConsoleAccount)
            .where(*self._active_conditions(tenant_id))
            .order_by(ConsoleAccount.username)
            .limit(limit)
            .offset(offset)
        )
        return list(result.all())

    async def count(self, tenant_id: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(ConsoleAccount)
            .where(*self._active_conditions(tenant_id))
        )
        return int(total or 0)

    async def count_all(self) -> int:
        total = await self._session.scalar(
            select(func.count()).select_from(ConsoleAccount).where(ConsoleAccount.is_deleted.is_(False))
        )
        return int(total or 0)

    async def add(self, account: ConsoleAccount) -> ConsoleAccount:
        self._session.add(account)
        await self._session.flush()
        return account
