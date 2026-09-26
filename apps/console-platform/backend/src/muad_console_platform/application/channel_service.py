import hashlib
import json
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from muad_api import AppError, validate_page
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    BotSnapshotItem,
    BotSnapshotResponse,
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.channel import (
    BIND_CODE_STATUS_ACTIVE,
    BIND_CODE_STATUS_EXPIRED,
    BIND_CODE_STATUS_REVOKED,
    BIND_CODE_STATUS_USED,
    BindCode,
    ChannelIdentity,
)
from ..infrastructure.models.control import SkillImportIdempotency
from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.bind_code_repository import BindCodeRepository
from ..infrastructure.repositories.bot_account_repository import BotAccountRepository
from ..infrastructure.repositories.channel_identity_repository import ChannelIdentityRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_service import AuditActor, AuditService

HASH_PREFIX = "sha256:"
PLATFORM_USER_STATUS_ACTIVE = "ACTIVE"
BIND_CODE_TTL = timedelta(minutes=10)
BIND_CODE_ALPHABET = string.ascii_uppercase + string.digits
BIND_CODE_LENGTH = 12
IDEMPOTENCY_ENDPOINT = "/internal/channel/bind"
FINGERPRINT_PREFIX = "sha256:"
AUDIT_RESOURCE_TYPE_BIND_CODE = "BIND_CODE"
AUDIT_RESOURCE_TYPE_IDENTITY = "CHANNEL_IDENTITY"


def generate_bind_code() -> str:
    return "".join(secrets.choice(BIND_CODE_ALPHABET) for _ in range(BIND_CODE_LENGTH))


def hash_bind_code(code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def bind_fingerprint(tenant_id: str, payload: ChannelBindRequest) -> str:
    """规范化 JSON 指纹：同 key 同指纹重放，异指纹 409 IDEMPOTENCY_MISMATCH。

    只把绑定码的 checksum 纳入指纹，不保存明文。
    """
    canonical = json.dumps(
        {
            "endpoint": IDEMPOTENCY_ENDPOINT,
            "tenant_id": tenant_id,
            "channel": payload.channel,
            "bot_id": payload.bot_id,
            "external_user_id": payload.external_user_id,
            "bind_code_checksum": hash_bind_code(payload.bind_code),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_identity_key(channel: str, bot_id: str, external_user_id: str) -> str:
    return f"{channel}:{bot_id}:{external_user_id}"


class ChannelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bots = BotAccountRepository(session)
        self._identities = ChannelIdentityRepository(session)
        self._bind_codes = BindCodeRepository(session)
        self._users = PlatformUserRepository(session)
        self._grants = AgentAccessGrantRepository(session)
        self._audit = AuditService(session)

    async def resolve(
        self,
        tenant_id: str,
        payload: ChannelResolveRequest,
    ) -> ChannelResolveResponse:
        bot = await self._bots.find_enabled(tenant_id, payload.bot_id)
        if bot is None:
            # 未知/禁用/已删除 bot 不是“未绑定用户”，按设计 E-01 返回 BOT_NOT_FOUND
            raise AppError(ErrorCode.BOT_NOT_FOUND)
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
        idempotency_key: str | None = None,
    ) -> ChannelBindResponse:
        fingerprint = bind_fingerprint(tenant_id, payload)
        if idempotency_key:
            await self._lock_idempotency(tenant_id, idempotency_key)
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, IDEMPOTENCY_ENDPOINT, fingerprint
            )
            if replayed is not None:
                return ChannelBindResponse.model_validate(replayed)
        response = await self._bind_once(tenant_id, payload)
        if idempotency_key:
            await self._record_idempotency(
                tenant_id,
                idempotency_key,
                IDEMPOTENCY_ENDPOINT,
                fingerprint,
                response.model_dump(mode="json"),
            )
        return response

    async def _lock_idempotency(self, tenant_id: str, idempotency_key: str) -> None:
        """同一 (tenant, key, endpoint) 串行化，避免并发重复消费绑定码。"""
        digest = hashlib.sha256(
            f"{tenant_id}|{idempotency_key}|{IDEMPOTENCY_ENDPOINT}".encode()
        ).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _idempotency_replay(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        record = await self._find_idempotency(tenant_id, idempotency_key, endpoint)
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return dict(record.response_json)

    async def _find_idempotency(
        self, tenant_id: str, idempotency_key: str, endpoint: str
    ) -> SkillImportIdempotency | None:
        record: SkillImportIdempotency | None = await self._session.scalar(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == idempotency_key,
                SkillImportIdempotency.endpoint == endpoint,
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        return record

    async def _record_idempotency(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        self._session.add(
            SkillImportIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_fingerprint=fingerprint,
                response_json=response,
            )
        )
        await self._session.flush()

    async def _bind_once(
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
        elif identity.platform_user_id != bind_code.platform_user_id:
            # 身份已属于其他平台用户：拒绝且不消费码（保持 ACTIVE，解绑后可复用）
            raise AppError(ErrorCode.IDENTITY_ALREADY_BOUND)
        await self._bind_codes.consume(
            bind_code,
            channel_identity_id=identity.id,
            consumed_at=now,
        )
        return ChannelBindResponse(platform_user_id=identity.platform_user_id, bound=True)

    async def create_bind_code(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
    ) -> tuple[str, datetime]:
        user = await self._users.get(tenant_id, user_id)
        if user is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        now = datetime.now(UTC)
        await self._bind_codes.revoke_active(tenant_id, user_id, now)
        code = generate_bind_code()
        bind_code = await self._bind_codes.add(
            BindCode(
                tenant_id=tenant_id,
                platform_user_id=user_id,
                code_hash=hash_bind_code(code),
                status=BIND_CODE_STATUS_ACTIVE,
                expires_at=now + BIND_CODE_TTL,
                created_by=actor.account_id,
            )
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE_BIND_CODE,
            resource_id=bind_code.id,
            action="CREATE",
            before=None,
            after={"user_id": str(user_id), "expires_at": bind_code.expires_at.isoformat()},
        )
        return code, bind_code.expires_at

    async def list_identities(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        channel: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        return await self._identities.list_for_user(tenant_id, user_id, page, page_size, channel)

    async def unbind_identity(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        identity_id: uuid.UUID,
        actor: AuditActor,
    ) -> datetime:
        await self._require_user(tenant_id, user_id)
        identity = await self._identities.get_for_user(tenant_id, user_id, identity_id)
        if identity is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = {"channel": identity.channel, "external_user_id": identity.external_user_id}
        await self._identities.soft_delete(identity)
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE_IDENTITY,
            resource_id=identity.id,
            action="DELETE",
            before=before,
            after=None,
        )
        return identity.update_time

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def bots(
        self,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> BotSnapshotResponse:
        valid = validate_page(page, page_size)
        digest, total = await self._bots.enabled_snapshot_digest(tenant_id)
        accounts = await self._bots.list_enabled_page(
            tenant_id,
            limit=valid.page_size,
            offset=(valid.page - 1) * valid.page_size,
        )
        items = [
            BotSnapshotItem(
                bot_account_id=account.id,
                bot_id=account.bot_id,
                secret=account.secret,
                agent_id=account.agent_id,
                enabled=account.enabled,
            )
            for account in accounts
        ]
        return BotSnapshotResponse(
            revision=f"{HASH_PREFIX}{digest}",
            items=items,
            page=valid.page,
            page_size=valid.page_size,
            total=total,
        )

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
