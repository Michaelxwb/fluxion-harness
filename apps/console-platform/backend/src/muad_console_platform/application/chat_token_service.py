"""用户 Webchat Token 签发、重置与版本撤销（设计 §3.3、§3.4 API-03、§3.7）。

语义要点：

- **只存摘要**：原文由 `secrets.token_urlsafe(32)`（256 位熵）生成，只在本方法返回的
  `WebchatTokenIssued` 里可见一次；库里只有 SHA-256 摘要（与列 CHECK `^[0-9a-f]{64}$` 同口径），
  审计快照连摘要都不写（RULE-secret-001）。
- **版本基准与单胜**：签发/重置是"用户→Token"固定锁序下的读-改-写——先锁 `platform_user` 行，
  再锁当前未撤销 Token 行，因此 PUT 的 `expected_revision` 用真实行锁判定单胜：`0` 表示首签
  （必须尚无任何签发行），`N` 表示按最新签发行重置（撤销后重签得到更高 revision）；过期版本、
  响应丢失的重放一律 `REVISION_CONFLICT`，不再次轮换、不回放明文。
- **撤销不删行**：重置先撤销旧行与其全部活跃会话，再新增 `revision+1` 行；**过期但未撤销**的
  行同样是 live，必须先显式撤销再重签，保证每用户至多一个未撤销 Token。
- **版本撤销幂等**：`revoke_token` 只作用于指定 `revision` 的行（及其关联会话）；旧版本重放不
  撤销后续新 Token，也不重复改时间戳；登出只撤销 CHAT 会话（TASK-041），不经过本方法。
- **期限唯一来源**：每次签发读一次当前平台设置（`webchat.access_ttl_days`；无版本行即 schema
  默认 30 天），配置读取发生在行锁之外；设置源损坏显式失败（`PLATFORM_SETTINGS_UNAVAILABLE`），
  不静默回落默认值。已签发行的 `expires_at` 冻结，改设置只影响后续签发。
- **用户状态**：签发/重置只核验用户归属存在；用户是否可用在 CHAT 交换时复核（API-04），
  禁用用户仍可由管理员撤销 Token。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import PlatformSettingsError
from muad_contracts.webchat import WebchatTokenIssued, WebchatTokenMetadata
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.auth import ChatAccessToken
from ..infrastructure.repositories.chat_token_repository import ChatTokenRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_service import AuditActor, AuditService
from .platform_settings_service import PlatformSettingsService

AUDIT_RESOURCE_TYPE = "CHAT_ACCESS_TOKEN"
ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_DELETE = "DELETE"
#: Token 熵：32 字节 = 256 位（url-safe base64 编码后 43 个字符）。
TOKEN_ENTROPY_BYTES = 32

#: API-03 元信息的四态（与 `WebchatTokenMetadata.status` 的字面量集合一致）。
TokenStatus = Literal["NOT_CONFIGURED", "ACTIVE", "EXPIRED", "REVOKED"]

_logger = logging.getLogger(__name__)


def generate_chat_token() -> str:
    """密码学安全随机的不透明 Token 原文（仅签发响应可见一次，不落库、不进日志）。"""
    return secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_chat_token(token: str) -> str:
    """Token 原文的 SHA-256 小写十六进制摘要（口径同库列 CHECK 与 Console 会话 Token）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_snapshot(row: ChatAccessToken | None) -> dict[str, Any] | None:
    """审计快照只列非凭据字段：**不含摘要、绝不含原文**。"""
    if row is None:
        return None
    return {
        "revision": row.revision,
        "revoked": row.revoked_at is not None,
        "issued_at": row.issued_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
    }


def _metadata(row: ChatAccessToken | None, *, now: datetime) -> WebchatTokenMetadata:
    """元信息投影：未配置 / ACTIVE / EXPIRED / REVOKED，永不携带原文。"""
    if row is None:
        return WebchatTokenMetadata(
            configured=False,
            status="NOT_CONFIGURED",
            token_id=None,
            revision=0,
            issued_at=None,
            expires_at=None,
        )
    if row.revoked_at is not None:
        status: TokenStatus = "REVOKED"
    elif row.expires_at <= now:
        status = "EXPIRED"
    else:
        status = "ACTIVE"
    return WebchatTokenMetadata(
        configured=True,
        status=status,
        token_id=row.id,
        revision=row.revision,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
    )


def _next_revision(latest: ChatAccessToken | None, expected_revision: int) -> int:
    """PUT 的版本判定：`0`=首签（尚无任何签发行），`N`=按最新签发行重置；其余一律冲突。

    基准是最新**签发行**（含已撤销/已过期者，撤销不删行）：撤销后按 GET 看到的最新版本重签
    得到更高 revision；响应丢失的重放与过期版本都撞 `REVISION_CONFLICT`。
    """
    if latest is None:
        if expected_revision == 0:
            return 1
        raise AppError(ErrorCode.REVISION_CONFLICT)
    if latest.revision != expected_revision:
        raise AppError(ErrorCode.REVISION_CONFLICT)
    return latest.revision + 1


class ChatTokenService:
    """Console 侧用户 Token Owner（API-03）：签发/重置、版本撤销与只读元信息。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tokens = ChatTokenRepository(session)
        self._users = PlatformUserRepository(session)
        self._audit = AuditService(session)

    async def read_metadata(self, tenant_id: str, user_id: uuid.UUID) -> WebchatTokenMetadata:
        """GET：只读元信息（最新版本行），原文与摘要都不返回。"""
        row = await self._tokens.find_latest(tenant_id, user_id)
        return _metadata(row, now=datetime.now(UTC))

    async def issue_token(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        expected_revision: int,
        actor: AuditActor,
    ) -> WebchatTokenIssued:
        """PUT：首签或重置；原文只在本返回值里出现一次，旧 Token 及关联会话同事务撤销。"""
        ttl = await self._access_ttl(tenant_id)
        if await self._users.get_for_update(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        latest = await self._tokens.find_latest(tenant_id, user_id)
        revision = _next_revision(latest, expected_revision)
        live = await self._tokens.find_live_for_update(tenant_id, user_id)
        now = datetime.now(UTC)
        if live is not None:
            await self._replace_live_token(tenant_id, actor, live, now)
        token = generate_chat_token()
        row = ChatAccessToken(
            tenant_id=tenant_id,
            platform_user_id=user_id,
            token_hash=hash_chat_token(token),
            revision=revision,
            issued_at=now,
            expires_at=now + ttl,
        )
        await self._tokens.add(row)
        await self._record_audit(
            tenant_id, actor, ACTION_CREATE, row, before=None, after=token_snapshot(row)
        )
        return WebchatTokenIssued(**(_metadata(row, now=now).model_dump()), token=SecretStr(token))

    async def revoke_token(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        expected_revision: int,
        actor: AuditActor,
    ) -> WebchatTokenMetadata:
        """DELETE：按指定版本幂等撤销（含关联会话）；旧版本重放不影响后续新 Token。"""
        if await self._users.get_for_update(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        row = await self._tokens.find_by_revision(tenant_id, user_id, expected_revision)
        now = datetime.now(UTC)
        if row is not None and row.revoked_at is None:
            before = token_snapshot(row)
            revoked_sessions = await self._tokens.revoke(row, now)
            await self._record_audit(tenant_id, actor, ACTION_DELETE, row, before=before, after=None)
            _logger.info(
                "chat_access_token_revoked",
                extra={"fields": {"token_id": str(row.id), "revision": row.revision,
                                  "revoked_sessions": revoked_sessions}},
            )
        return _metadata(await self._tokens.find_latest(tenant_id, user_id), now=now)

    async def _replace_live_token(
        self, tenant_id: str, actor: AuditActor, live: ChatAccessToken, revoked_at: datetime
    ) -> None:
        """重置的旧版本落点：撤销行与其全部活跃会话，并留下 UPDATE 审计痕迹。"""
        before = token_snapshot(live)
        revoked_sessions = await self._tokens.revoke(live, revoked_at)
        await self._record_audit(
            tenant_id, actor, ACTION_UPDATE, live, before=before, after=token_snapshot(live)
        )
        _logger.info(
            "chat_access_token_replaced",
            extra={"fields": {"token_id": str(live.id), "revision": live.revision,
                              "revoked_sessions": revoked_sessions}},
        )

    async def _access_ttl(self, tenant_id: str) -> timedelta:
        """签发期限：当前设置版本的 `webchat.access_ttl_days`（缺省即 schema 默认 30 天）。

        配置读取在用户行锁之外；设置源损坏显式失败，不静默回落默认值（design §3.7）。
        """
        try:
            snapshot = await PlatformSettingsService(self._session).read_current(tenant_id)
        except PlatformSettingsError as exc:
            raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE) from exc
        return timedelta(days=snapshot.settings.webchat.access_ttl_days)

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        row: ChatAccessToken,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        """审计与业务同事务（同一 session）；快照只含非凭据字段。"""
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=row.id,
            action=action,
            before=before,
            after=after,
        )
