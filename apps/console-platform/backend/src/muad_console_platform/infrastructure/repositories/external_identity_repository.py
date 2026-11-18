"""外部身份与入口授权的读写。

所有查询都带 ``tenant_id``：身份按 ``(tenant, platform_key, identity_hash)`` 直接命中，
不扫用户表；入口反查按 ``(tenant, bot_account_id)`` 走 grant 的复合索引。
"""

import collections.abc
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BotAccount, EntryAccessGrant, ExternalIdentity
from ..models.control import AgentDefinition, PlatformUser


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

    async def find_user_auth_revision(self, tenant_id: str, platform_user_id: uuid.UUID) -> int | None:
        """取平台用户的权限版本号（`/identity` 的 `auth_revision`，API-01）。

        外部平台用它判断自己的授权缓存是否过期；用户不存在（已删）时返回 None，不编造 0。
        """
        return await self._session.scalar(
            select(PlatformUser.auth_revision).where(
                PlatformUser.tenant_id == tenant_id,
                PlatformUser.id == platform_user_id,
                PlatformUser.is_deleted.is_(False),
            )
        )

    async def list_grants_with_agent(
        self,
        tenant_id: str,
        identity_ids: list[uuid.UUID],
    ) -> list[tuple[EntryAccessGrant, BotAccount, bool | None]]:
        """取授权 + 入口 + 其 Agent 是否启用，供 `/identity` 做逐入口裁决（API-01）。

        与 `list_grants` 分开是为了不动 Gateway 热路径：那条路只要「有哪些 Agent」，
        不需要 Agent 启用状态，也不该为它多 join 一张表。

        第三个元素为 `None` 表示 Agent 行不存在（被删除）而非「停用」——两者对外的
        `reason` 不同（`NO_AGENT_GRANT` vs `AGENT_DISABLED`），不能合并。
        """
        if not identity_ids:
            return []
        rows = await self._session.execute(
            select(EntryAccessGrant, BotAccount, AgentDefinition.enabled)
            .join(BotAccount, BotAccount.id == EntryAccessGrant.bot_account_id)
            .outerjoin(
                AgentDefinition,
                (AgentDefinition.id == BotAccount.agent_id)
                & (AgentDefinition.tenant_id == tenant_id)
                & (AgentDefinition.is_deleted.is_(False)),
            )
            .where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.external_identity_id.in_(identity_ids),
                EntryAccessGrant.is_deleted.is_(False),
                BotAccount.tenant_id == tenant_id,
                BotAccount.is_deleted.is_(False),
            )
            .order_by(EntryAccessGrant.create_time.asc(), EntryAccessGrant.id.asc())
        )
        return [(grant, bot, enabled) for grant, bot, enabled in rows.all()]

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

    async def get_entry(
        self,
        tenant_id: str,
        bot_account_id: uuid.UUID,
    ) -> BotAccount | None:
        """按入口 id 取 bot_account；跨租户一律返回 None。"""
        bot: BotAccount | None = await self._session.scalar(
            select(BotAccount).where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.id == bot_account_id,
                BotAccount.is_deleted.is_(False),
            )
        )
        return bot

    async def list_entry_candidates(
        self,
        tenant_id: str,
        channel: str,
        query: str,
        offset: int,
        limit: int,
    ) -> list[BotAccount]:
        """可绑定入口候选：服务端过滤 + 分页，避免前端本地过滤冒充完整搜索。"""
        stmt = select(BotAccount).where(
            BotAccount.tenant_id == tenant_id,
            BotAccount.channel == channel,
            BotAccount.is_deleted.is_(False),
        )
        if query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(or_(BotAccount.name.ilike(pattern), BotAccount.bot_id.ilike(pattern)))
        stmt = stmt.order_by(BotAccount.create_time.desc(), BotAccount.id.desc())
        rows = await self._session.scalars(stmt.offset(offset).limit(limit))
        return list(rows)

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

    async def list_platform_keys_for_users(
        self,
        tenant_id: str,
        user_ids: collections.abc.Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[str, ...]]:
        """``user_id → 其存在的平台列表``，用于停用用户/删除入口时定向清理凭证。"""
        ids = list(user_ids)
        if not ids:
            return {}
        rows = await self._session.execute(
            select(ExternalIdentity.platform_user_id, ExternalIdentity.platform_key)
            .where(
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.platform_user_id.in_(ids),
                ExternalIdentity.is_deleted.is_(False),
            )
            .distinct()
        )
        grouped: dict[uuid.UUID, list[str]] = {}
        for user_id, platform_key in rows:
            grouped.setdefault(user_id, []).append(platform_key)
        return {user_id: tuple(keys) for user_id, keys in grouped.items()}

    async def list_user_ids_for_bot(
        self,
        tenant_id: str,
        bot_account_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """入口停用/删除时反查受影响的用户（认证缓存失效范围）。"""
        rows = await self._session.scalars(
            select(ExternalIdentity.platform_user_id)
            .join(
                EntryAccessGrant,
                EntryAccessGrant.external_identity_id == ExternalIdentity.id,
            )
            .where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.bot_account_id == bot_account_id,
                EntryAccessGrant.is_deleted.is_(False),
                ExternalIdentity.is_deleted.is_(False),
            )
            .distinct()
        )
        return tuple(dict.fromkeys(rows))

    async def add_grant(self, grant: EntryAccessGrant) -> EntryAccessGrant:
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def get_grant_for_identity(
        self,
        tenant_id: str,
        identity_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> EntryAccessGrant | None:
        """按入口（`bot_account_id`）取授权，同时核身份。

        入口是授权接口（`channel_account_id`）使用的自然键，解绑沿用同一口径，
        调用方无需先知道 grant 行主键。核身份仍能防止拿别的身份的入口来解绑。
        """
        grant: EntryAccessGrant | None = await self._session.scalar(
            select(EntryAccessGrant).where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.external_identity_id == identity_id,
                EntryAccessGrant.bot_account_id == entry_id,
                EntryAccessGrant.is_deleted.is_(False),
            )
        )
        return grant

    async def soft_delete_identity(self, tenant_id: str, identity_id: uuid.UUID) -> None:
        """解绑身份：同时软删其入口授权，避免留下指向已删身份的 grant。"""
        identity = await self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.id == identity_id,
                ExternalIdentity.is_deleted.is_(False),
            )
        )
        if identity is None:
            return
        identity.is_deleted = True
        grants = await self._session.scalars(
            select(EntryAccessGrant).where(
                EntryAccessGrant.tenant_id == tenant_id,
                EntryAccessGrant.external_identity_id == identity_id,
                EntryAccessGrant.is_deleted.is_(False),
            )
        )
        for grant in grants:
            grant.is_deleted = True
        await self._session.flush()
