"""外部身份与入口授权的读写。

所有查询都带 ``tenant_id``：身份按 ``(tenant, platform_key, identity_hash)`` 直接命中，
不扫用户表；入口反查按 ``(tenant, bot_account_id)`` 走 grant 的复合索引。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BotAccount, EntryAccessGrant, ExternalIdentity


class ExternalIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_hash(
        self,
        tenant_id: str,
        platform_key: str,
        identity_hash: str,
    ) -> ExternalIdentity | None:
        """按身份指纹定位。跨租户不可见；不存在的租户与不存在的身份返回同样的 None。"""
        identity: ExternalIdentity | None = await self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.platform_key == platform_key,
                ExternalIdentity.identity_hash == identity_hash,
                ExternalIdentity.is_deleted.is_(False),
            )
        )
        return identity

    async def get_for_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
    ) -> ExternalIdentity | None:
        identity: ExternalIdentity | None = await self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.id == identity_id,
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.platform_user_id == user_id,
                ExternalIdentity.is_deleted.is_(False),
            )
        )
        return identity

    async def list_for_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
    ) -> list[ExternalIdentity]:
        rows = await self._session.scalars(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.platform_user_id == user_id,
                ExternalIdentity.is_deleted.is_(False),
            )
            .order_by(ExternalIdentity.create_time.asc(), ExternalIdentity.id.asc())
        )
        return list(rows)

    async def add(self, identity: ExternalIdentity) -> ExternalIdentity:
        self._session.add(identity)
        await self._session.flush()
        return identity

    # -- entry grants -----------------------------------------------------

    async def list_grants(
        self,
        tenant_id: str,
        identity_ids: list[uuid.UUID],
    ) -> list[tuple[EntryAccessGrant, BotAccount]]:
        """批量取授权及其入口（避免逐身份查询造成 N+1）。"""
        if not identity_ids:
            return []
        rows = await self._session.execute(
            select(EntryAccessGrant, BotAccount)
            .join(BotAccount, BotAccount.id == EntryAccessGrant.bot_account_id)
            .where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.external_identity_id.in_(identity_ids),
                EntryAccessGrant.is_deleted.is_(False),
                BotAccount.tenant_id == tenant_id,
                BotAccount.is_deleted.is_(False),
            )
            .order_by(EntryAccessGrant.create_time.asc(), EntryAccessGrant.id.asc())
        )
        return [(grant, bot) for grant, bot in rows.all()]

    async def find_grant(
        self,
        tenant_id: str,
        identity_id: uuid.UUID,
        bot_account_id: uuid.UUID,
    ) -> EntryAccessGrant | None:
        grant: EntryAccessGrant | None = await self._session.scalar(
            select(EntryAccessGrant).where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.external_identity_id == identity_id,
                EntryAccessGrant.bot_account_id == bot_account_id,
                EntryAccessGrant.is_deleted.is_(False),
            )
        )
        return grant

    async def list_identity_ids_for_bot(
        self,
        tenant_id: str,
        bot_account_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """入口停用/删除时反查受影响的身份，用于批量失效。"""
        rows = await self._session.scalars(
            select(EntryAccessGrant.external_identity_id).where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.bot_account_id == bot_account_id,
                EntryAccessGrant.is_deleted.is_(False),
            )
        )
        return list(rows)

    async def add_grant(self, grant: EntryAccessGrant) -> EntryAccessGrant:
        self._session.add(grant)
        await self._session.flush()
        return grant
