import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import ChannelIdentity


class ChannelIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self,
        tenant_id: str,
        channel: str,
        bot_account_id: uuid.UUID,
        external_user_id: str,
    ) -> ChannelIdentity | None:
        identity: ChannelIdentity | None = await self._session.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.tenant_id == tenant_id,
                ChannelIdentity.channel == channel,
                ChannelIdentity.bot_account_id == bot_account_id,
                ChannelIdentity.external_user_id == external_user_id,
                ChannelIdentity.is_deleted.is_(False),
            )
        )
        return identity

    async def add(self, identity: ChannelIdentity) -> ChannelIdentity:
        self._session.add(identity)
        await self._session.flush()
        return identity
