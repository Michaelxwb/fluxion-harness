import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BotAccount, ChannelIdentity
from ..models.control import PlatformUser


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

    async def get_for_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
    ) -> ChannelIdentity | None:
        identity: ChannelIdentity | None = await self._session.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.id == identity_id,
                ChannelIdentity.tenant_id == tenant_id,
                ChannelIdentity.platform_user_id == user_id,
                ChannelIdentity.is_deleted.is_(False),
            )
        )
        return identity

    async def list_for_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        channel: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [
            ChannelIdentity.tenant_id == tenant_id,
            ChannelIdentity.platform_user_id == user_id,
            ChannelIdentity.is_deleted.is_(False),
        ]
        if channel:
            conditions.append(ChannelIdentity.channel == channel)
        total = await self._session.scalar(
            select(func.count()).select_from(ChannelIdentity).where(*conditions)
        )
        rows = (
            await self._session.execute(
                select(
                    ChannelIdentity.id,
                    ChannelIdentity.channel,
                    ChannelIdentity.external_user_id,
                    BotAccount.bot_id,
                    ChannelIdentity.bound_at,
                    ChannelIdentity.last_active_at,
                    PlatformUser.status.label("user_status"),
                )
                .join(BotAccount, BotAccount.id == ChannelIdentity.bot_account_id)
                .join(PlatformUser, PlatformUser.id == ChannelIdentity.platform_user_id)
                .where(*conditions)
                .order_by(ChannelIdentity.bound_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def soft_delete(self, identity: ChannelIdentity) -> None:
        identity.is_deleted = True
        await self._session.flush()
