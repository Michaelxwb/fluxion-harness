import hashlib
import json
import uuid
from datetime import UTC, datetime

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    BotSnapshotItem,
    BotSnapshotResponse,
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.channel import (
    BIND_CODE_STATUS_ACTIVE,
    BIND_CODE_STATUS_EXPIRED,
    BIND_CODE_STATUS_REVOKED,
    BIND_CODE_STATUS_USED,
    ChannelIdentity,
)
from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.bind_code_repository import BindCodeRepository
from ..infrastructure.repositories.bot_account_repository import BotAccountRepository
from ..infrastructure.repositories.channel_identity_repository import ChannelIdentityRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository

HASH_PREFIX = "sha256:"
PLATFORM_USER_STATUS_ACTIVE = "ACTIVE"


def hash_bind_code(code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def build_identity_key(channel: str, bot_id: str, external_user_id: str) -> str:
    return f"{channel}:{bot_id}:{external_user_id}"


class ChannelService:
    def __init__(self, session: AsyncSession) -> None:
        self._bots = BotAccountRepository(session)
        self._identities = ChannelIdentityRepository(session)
        self._bind_codes = BindCodeRepository(session)
        self._users = PlatformUserRepository(session)
        self._grants = AgentAccessGrantRepository(session)

    async def resolve(
        self,
        tenant_id: str,
        payload: ChannelResolveRequest,
    ) -> ChannelResolveResponse:
        bot = await self._bots.find_enabled(tenant_id, payload.bot_id)
        if bot is None:
            return ChannelResolveResponse(
                bound=False,
                agent_id=None,
                platform_user_id=None,
                authorized=False,
            )
        identity = await self._identities.find(
            tenant_id,
            payload.channel,
            bot.id,
            payload.external_user_id,
        )
        if identity is None:
            return ChannelResolveResponse(
                bound=False,
                agent_id=bot.agent_id,
                platform_user_id=None,
                authorized=False,
            )
        authorized = await self._is_authorized(
            tenant_id,
            identity.platform_user_id,
            bot.agent_id,
        )
        return ChannelResolveResponse(
            bound=True,
            agent_id=bot.agent_id,
            platform_user_id=identity.platform_user_id,
            authorized=authorized,
        )

    async def bind(
        self,
        tenant_id: str,
        payload: ChannelBindRequest,
    ) -> ChannelBindResponse:
        bot = await self._bots.find_enabled(tenant_id, payload.bot_id)
        if bot is None:
            raise AppError(ErrorCode.BOT_NOT_FOUND)
        bind_code = await self._bind_codes.find_for_update(
            tenant_id,
            hash_bind_code(payload.bind_code),
        )
        if bind_code is None:
            raise AppError(ErrorCode.BIND_CODE_INVALID)
        now = datetime.now(UTC)
        if bind_code.status == BIND_CODE_STATUS_USED or bind_code.status == BIND_CODE_STATUS_REVOKED:
            raise AppError(ErrorCode.BIND_CODE_INVALID)
        if bind_code.status == BIND_CODE_STATUS_EXPIRED or bind_code.expires_at <= now:
            raise AppError(ErrorCode.BIND_CODE_EXPIRED)
        if bind_code.status != BIND_CODE_STATUS_ACTIVE:
            raise AppError(ErrorCode.BIND_CODE_INVALID)
        identity = await self._identities.find(
            tenant_id,
            payload.channel,
            bot.id,
            payload.external_user_id,
        )
        if identity is None:
            identity = await self._identities.add(
                ChannelIdentity(
                    tenant_id=tenant_id,
                    channel=payload.channel,
                    identity_key=build_identity_key(
                        payload.channel,
                        bot.bot_id,
                        payload.external_user_id,
                    ),
                    external_user_id=payload.external_user_id,
                    bot_account_id=bot.id,
                    platform_user_id=bind_code.platform_user_id,
                    bound_at=now,
                )
            )
        await self._bind_codes.consume(
            bind_code,
            channel_identity_id=identity.id,
            consumed_at=now,
        )
        return ChannelBindResponse(platform_user_id=identity.platform_user_id, bound=True)

    async def bots(self, tenant_id: str) -> BotSnapshotResponse:
        accounts = await self._bots.list_enabled(tenant_id)
        items = [
            BotSnapshotItem(
                bot_account_id=account.id,
                bot_id=account.bot_id,
                secret_ref=account.secret_ref,
                agent_id=account.agent_id,
                enabled=account.enabled,
            )
            for account in accounts
        ]
        canonical = json.dumps(
            [item.model_dump(mode="json") for item in items],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        revision = f"{HASH_PREFIX}{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
        return BotSnapshotResponse(revision=revision, items=items)

    async def _is_authorized(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        status = await self._users.get_status(tenant_id, user_id)
        if status != PLATFORM_USER_STATUS_ACTIVE:
            return False
        return await self._grants.has_active_grant(tenant_id, user_id, agent_id)
