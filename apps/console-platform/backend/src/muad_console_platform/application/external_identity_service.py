"""外部身份与入口授权的管理面服务（API-17 ~ API-24）。

身份是用户级的，入口授权是入口级的 —— 两者分开表达：
登记一个 ``expert_id``+``tenant_id`` 只回答「这是谁」，能否在某个入口使用
取决于 ``entry_access_grant``。缺任何一步都不能放行（见 RULE-data-002）。
"""

import uuid
from datetime import UTC, datetime

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.channel import BotAccount, EntryAccessGrant, ExternalIdentity
from ..infrastructure.models.control import PlatformUser
from ..infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)
from .dto import (
    ExternalEntryGrantItem,
    ExternalEntryItem,
    ExternalIdentityItem,
    ExternalPlatformItem,
)
from .external_assertion.config import ExternalPlatformConfig, get_platform_configs
from .external_assertion.principal import compute_identity_hash

MSSW_PLATFORM_KEY = "mssw"


class ExternalIdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ExternalIdentityRepository(session)

    # ------------------------------------------------------------------ 平台与入口

    def list_platforms(self) -> list[ExternalPlatformItem]:
        """API-17：只暴露管理员需要的元数据，**不含任何验签因子**。"""
        configs = get_platform_configs()
        if not configs:
            return []
        return [
            ExternalPlatformItem(
                platform_key=config.platform_key,
                display_name=config.display_name,
                configured=True,
                identity_keys=list(config.identity_keys),
                identity_field_labels=dict(config.identity_field_labels),
            )
            for config in configs.values()
        ]

    # ------------------------------------------------------------------ 身份

    async def list_identities(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
    ) -> list[ExternalIdentityItem]:
        """API-24：用户级身份 + 各自入口授权，一次查询取完 grant，避免逐条回查。"""
        records = await self._repo.list_for_user(tenant_id, user_id)
        if not records:
            return []
        entries = await self._repo.list_grants(
            tenant_id, [record.id for record in records]
        )
        grants_by_identity: dict[uuid.UUID, list[ExternalEntryGrantItem]] = {
            record.id: [] for record in records
        }
        for grant, bot_account in entries:
            grants_by_identity.setdefault(grant.external_identity_id, []).append(
                ExternalEntryGrantItem(
                    entry_id=bot_account.id,
                    bot_id=bot_account.bot_id,
                    name=bot_account.name,
                )
            )
        return [
            ExternalIdentityItem(
                identity_id=record.id,
                platform_key=record.platform_key,
                identity=dict(record.identity_json),
                status=record.status,
                grants=grants_by_identity.get(record.id, []),
            )
            for record in records
        ]

    async def upsert_identity(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        platform_key: str,
        identity: dict[str, str],
    ) -> ExternalIdentityItem:
        """API-18：新增身份。字段集合必须与平台 `identity_keys` 完全一致。"""
        config = self._require_platform(platform_key)
        normalized = self._validate_identity(config, identity)
        identity_hash = compute_identity_hash(normalized)

        await self._require_user(tenant_id, user_id)

        existing = await self._repo.find_by_hash(tenant_id, platform_key, identity_hash)
        if existing is not None:
            if existing.platform_user_id != user_id:
                # 同一身份不能挂到两个用户；重复提交同一用户则幂等返回。
                raise AppError(ErrorCode.IDENTITY_ALREADY_BOUND)
            return await self._as_item(tenant_id, existing)

        record = ExternalIdentity(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            platform_key=platform_key,
            platform_user_id=user_id,
            identity_hash=identity_hash,
            identity_json=normalized,
            status="ACTIVE",
        )
        await self._repo.add(record)
        await self._touch_auth_revision(tenant_id, user_id)
        return await self._as_item(tenant_id, record)

    async def update_identity(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
        identity: dict[str, str],
    ) -> ExternalIdentityItem:
        """API-21：编辑身份。重算 hash，历史会话归属不变。"""
        record = await self._repo.get_for_user(tenant_id, user_id, identity_id)
        if record is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        config = self._require_platform(record.platform_key)
        normalized = self._validate_identity(config, identity)
        new_hash = compute_identity_hash(normalized)

        if new_hash != record.identity_hash:
            conflict = await self._repo.find_by_hash(tenant_id, record.platform_key, new_hash)
            if conflict is not None and conflict.id != record.id:
                raise AppError(ErrorCode.IDENTITY_ALREADY_BOUND)
            record.identity_hash = new_hash
        record.identity_json = normalized
        await self._session.flush()
        await self._touch_auth_revision(tenant_id, user_id)
        return await self._as_item(tenant_id, record)

    async def delete_identity(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
    ) -> None:
        record = await self._repo.get_for_user(tenant_id, user_id, identity_id)
        if record is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        await self._repo.soft_delete_identity(tenant_id, identity_id)
        await self._touch_auth_revision(tenant_id, user_id)

    # ------------------------------------------------------------------ 入口授权

    async def list_entries(
        self,
        tenant_id: str,
        platform_key: str,
        user_id: uuid.UUID | None,
        query: str,
        page: int,
        page_size: int,
    ) -> list[ExternalEntryItem]:
        """API-19：可绑定的 MSSWWEB 入口，分页搜索。

        由服务端分页与过滤；前端不得用本地过滤冒充完整搜索，否则大租户下漏项。
        """
        self._require_platform(platform_key)
        offset = max(page - 1, 0) * page_size
        bot_accounts = await self._repo.list_entry_candidates(
            tenant_id,
            channel="MSSWWEB",
            query=query,
            offset=offset,
            limit=page_size,
        )
        granted: set[uuid.UUID] = set()
        if user_id is not None:
            identities = await self._repo.list_for_user(tenant_id, user_id)
            if identities:
                entries = await self._repo.list_grants(
                    tenant_id, [record.id for record in identities]
                )
                granted = {bot.id for _, bot in entries}
        return [
            ExternalEntryItem(
                entry_id=bot.id,
                bot_id=bot.bot_id,
                name=bot.name,
                channel=bot.channel,
                agent_id=bot.agent_id,
                agent_name="",
                granted=bot.id in granted,
            )
            for bot in bot_accounts
        ]

    async def grant_entry(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
        channel_account_id: uuid.UUID,
    ) -> ExternalIdentityItem:
        """API-22：把身份授权到某个入口。重复授权幂等。"""
        record = await self._repo.get_for_user(tenant_id, user_id, identity_id)
        if record is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        bot = await self._repo.get_entry(tenant_id, channel_account_id)
        if bot is None:
            # 入口不存在或不属于本租户：统一报未找到，不泄露跨租户存在性。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

        existing = await self._repo.find_grant(tenant_id, identity_id, channel_account_id)
        if existing is None:
            await self._repo.add_grant(
                EntryAccessGrant(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    external_identity_id=identity_id,
                    bot_account_id=channel_account_id,
                )
            )
        await self._touch_auth_revision(tenant_id, user_id)
        return await self._as_item(tenant_id, record)

    async def revoke_grant(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
        grant_id: uuid.UUID,
    ) -> None:
        """API-23：解绑入口。只删 grant，身份保留，重绑后历史仍归属该用户。"""
        record = await self._repo.get_for_user(tenant_id, user_id, identity_id)
        if record is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        grant = await self._repo.get_grant_for_identity(tenant_id, identity_id, grant_id)
        if grant is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        grant.is_deleted = True
        await self._session.flush()
        await self._touch_auth_revision(tenant_id, user_id)

    # ------------------------------------------------------------------ 内部

    def _require_platform(self, platform_key: str) -> ExternalPlatformConfig:
        config = get_platform_configs().get(platform_key)
        if config is None:
            raise AppError(ErrorCode.EXTERNAL_PLATFORM_NOT_CONFIGURED)
        return config

    @staticmethod
    def _validate_identity(
        config: ExternalPlatformConfig,
        identity: dict[str, str],
    ) -> dict[str, str]:
        """字段集合必须与 `identity_keys` 完全一致，且每个值非空。"""
        expected = set(config.identity_keys)
        actual = set(identity.keys())
        if actual != expected:
            raise AppError(ErrorCode.IDENTITY_KEYS_MISMATCH)
        normalized: dict[str, str] = {}
        for key in config.identity_keys:
            value = identity[key]
            if not isinstance(value, str) or not value.strip():
                raise AppError(ErrorCode.IDENTITY_KEYS_MISMATCH)
            normalized[key] = value
        return normalized

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        user = await self._session.get(PlatformUser, user_id)
        if user is None or user.is_deleted or user.tenant_id != tenant_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _touch_auth_revision(self, tenant_id: str, user_id: uuid.UUID) -> None:
        """任何影响可访问性的变更都要递增版本，供认证缓存失效使用（TASK-006 消费）。"""
        user = await self._session.get(PlatformUser, user_id)
        if user is not None and user.tenant_id == tenant_id:
            user.auth_revision = (user.auth_revision or 1) + 1
            user.update_time = datetime.now(UTC)

    async def _as_item(self, tenant_id: str, record: ExternalIdentity) -> ExternalIdentityItem:
        entries = await self._repo.list_grants(tenant_id, [record.id])
        return ExternalIdentityItem(
            identity_id=record.id,
            platform_key=record.platform_key,
            identity=dict(record.identity_json),
            status=record.status,
            grants=[
                ExternalEntryGrantItem(entry_id=bot.id, bot_id=bot.bot_id, name=bot.name)
                for _, bot in entries
            ],
        )
