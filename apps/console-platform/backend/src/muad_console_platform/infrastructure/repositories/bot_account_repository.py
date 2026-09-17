from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BotAccount


class BotAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_enabled(self, tenant_id: str, bot_id: str) -> BotAccount | None:
        account: BotAccount | None = await self._session.scalar(
            select(BotAccount).where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.bot_id == bot_id,
                BotAccount.enabled.is_(True),
                BotAccount.is_deleted.is_(False),
            )
        )
        return account

    async def list_enabled(self, tenant_id: str) -> list[BotAccount]:
        accounts = await self._session.scalars(
            select(BotAccount)
            .where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.enabled.is_(True),
                BotAccount.is_deleted.is_(False),
            )
            .order_by(BotAccount.bot_id)
        )
        return list(accounts.all())
