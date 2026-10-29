"""用户 Token 交换与独立 CHAT 会话生命周期（设计 §3.3、§3.4 API-04、§3.7）。

语义要点：

- **交换**：浏览器只提交用户 Token（没有 tenant/user 参数）。服务按 SHA-256 摘要定位 Token 行，
  从**记录**导出租户与用户，再按"用户 → Token"固定锁序（先锁 `platform_user` 行，再锁该用户
  当前未撤销的 Token 行）复核来源的撤销/到期与用户状态，然后生成**独立**随机会话原文：库里只存
  摘要，原文只在本方法返回的 `ChatSessionGrant` 里出现一次（HttpOnly Cookie 由 API-04 端点写入，
  本服务不碰 HTTP/Cookie）。
- **期限**：`chat_session.expires_at = min(交换时刻 + session_ttl_hours, 来源 Token 到期)`；时间取
  **PG 服务端** `now()`（同一事务内固定），跨实例不依赖机器墙钟。设置只影响本次交换，已建立的
  会话不漂移。
- **读取**：按摘要定位后实时校验——会话自身（未撤销/未过期）、来源 Token（未撤销/未过期）与用户
  （存在且 `ACTIVE`）三者都必须是**当前事实**；**不读平台设置**，所以设置源损坏不影响已认证请求，
  也绝不滑动续期（不改写任何行）。
- **登出**：只撤销本会话（幂等）；其他设备会话与来源 Token 都不受影响。来源 Token 被重置/撤销时
  的**关联会话撤销**由 `ChatTokenService` 在同一事务完成，不走本方法。
- **错误映射**（目录已由 TASK-007 登记，三处一致）：未知/已删除/非启用用户的 Token 一律
  `CHAT_TOKEN_INVALID`（不暴露用户是否存在），已撤销 `CHAT_TOKEN_REVOKED`，已到期
  `CHAT_TOKEN_EXPIRED`；会话侧未知摘要 `UNAUTHORIZED`，已撤销或来源/用户失效
  `CHAT_SESSION_REVOKED`，已到期 `CHAT_SESSION_EXPIRED`；设置源损坏
  `PLATFORM_SETTINGS_UNAVAILABLE`（503）。凭据判定先于设置读取，故基础设施故障不会掩盖 401。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import PlatformSettingsError
from muad_contracts.webchat import ChatAuthMethod, WebchatSessionInfo
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.auth import ChatAccessToken, ChatSession
from ..infrastructure.models.control import PlatformUser
from ..infrastructure.repositories.chat_session_repository import ChatSessionRepository
from ..infrastructure.repositories.chat_token_repository import ChatTokenRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .chat_token_service import generate_chat_token, hash_chat_token
from .platform_settings_service import PlatformSettingsService

#: 平台用户可用状态（与 `platform_user.status` 的写入口径一致；停用是实时门控，不是撤销）。
PLATFORM_USER_STATUS_ACTIVE = "ACTIVE"

#: 首期唯一认证方式（设计 §3.4：认证端口产出统一 principal）。
AUTH_METHOD_USER_TOKEN: ChatAuthMethod = "USER_TOKEN"

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatSessionGrant:
    """交换结果：公开会话信息 + **只进 HttpOnly Cookie** 的独立会话原文。

    `session_token` 用 `SecretStr` 承载（对象表示里只有 `**********`）；本对象不进审计、不进日志。
    """

    info: WebchatSessionInfo
    session_token: SecretStr
    #: 滑动续期的 idle TTL（设置 `webchat.session_ttl_hours`）；绝对上限是来源 Token 到期。
    idle_ttl_sec: int


class ChatSessionService:
    """Console 侧 CHAT 会话 Owner（API-04）：交换、读取与登出。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tokens = ChatTokenRepository(session)
        self._sessions = ChatSessionRepository(session)
        self._users = PlatformUserRepository(session)

    async def exchange(self, token: SecretStr) -> ChatSessionGrant:
        """API-04 交换：核验来源 Token/用户后签发独立会话；原文只在本返回值里出现一次。"""
        presented = await self._tokens.find_by_token_hash(hash_chat_token(token.get_secret_value()))
        if presented is None:
            raise AppError(ErrorCode.CHAT_TOKEN_INVALID)
        user = await self._users.get_for_update(presented.tenant_id, presented.platform_user_id)
        if user is None:
            raise AppError(ErrorCode.CHAT_TOKEN_INVALID)
        live = await self._tokens.find_live_for_update(presented.tenant_id, presented.platform_user_id)
        if live is None or live.id != presented.id:
            raise AppError(ErrorCode.CHAT_TOKEN_REVOKED)
        now = await self._pg_now()
        if live.expires_at <= now:
            raise AppError(ErrorCode.CHAT_TOKEN_EXPIRED)
        if user.status != PLATFORM_USER_STATUS_ACTIVE:
            raise AppError(ErrorCode.CHAT_TOKEN_INVALID)
        ttl = await self._session_ttl(presented.tenant_id)
        # 绝对期限取 min(交换 + session_ttl, 来源 Token 到期)：设置调大不能越过 Token，
        # 调小则让会话在设置窗口内结束；idle TTL 仍是设置值（滑动续期上限）。
        return await self._create_session(
            user,
            live,
            now=now,
            expires_at=min(now + ttl, live.expires_at),
            idle_ttl_sec=int(ttl.total_seconds()),
        )

    async def resolve(self, session_token: SecretStr) -> WebchatSessionInfo:
        """按摘要定位实时校验会话/来源 Token/用户；不读设置、不滑动续期、不改写任何行。

        读取热路径只发一条 JOIN SELECT：会话、来源 Token、用户与 PG `now()` 一次取齐。
        错误优先级保持原语义（未知会话 → 会话撤销/过期 → Token 撤销/过期 → 用户失效）。
        """
        facts = await self._sessions.find_facts_by_token_hash(
            hash_chat_token(session_token.get_secret_value())
        )
        if facts is None:
            raise AppError(ErrorCode.UNAUTHORIZED)
        row, token_row, user, now = facts.session, facts.token, facts.user, facts.db_now
        if row.revoked_at is not None:
            raise AppError(ErrorCode.CHAT_SESSION_REVOKED)
        if row.expires_at <= now:
            raise AppError(ErrorCode.CHAT_SESSION_EXPIRED)
        if token_row is None or token_row.revoked_at is not None:
            raise AppError(ErrorCode.CHAT_SESSION_REVOKED)
        if token_row.expires_at <= now:
            raise AppError(ErrorCode.CHAT_TOKEN_EXPIRED)
        if user is None or user.status != PLATFORM_USER_STATUS_ACTIVE:
            raise AppError(ErrorCode.CHAT_SESSION_REVOKED)
        return _info(
            tenant_id=row.tenant_id,
            user_id=user.id,
            display_name=user.display_name,
            session_id=row.id,
            expires_at=row.expires_at,
        )

    async def logout(self, session_token: SecretStr) -> None:
        """登出只撤本会话（幂等空操作）；不触碰其他设备会话与来源 Token。"""
        row = await self._sessions.find_by_token_hash(hash_chat_token(session_token.get_secret_value()))
        if row is None or row.revoked_at is not None:
            return
        await self._sessions.revoke(row, await self._pg_now())
        _logger.info(
            "chat_session_logged_out",
            extra={"fields": {"session_id": str(row.id), "tenant_id": row.tenant_id}},
        )

    async def _create_session(
        self,
        user: PlatformUser,
        token_row: ChatAccessToken,
        *,
        now: datetime,
        expires_at: datetime,
        idle_ttl_sec: int,
    ) -> ChatSessionGrant:
        """落库独立会话（只存摘要）并返回一次性原文；提交由调用方决定。"""
        session_token = generate_chat_token()
        row = ChatSession(
            tenant_id=token_row.tenant_id,
            access_token_id=token_row.id,
            platform_user_id=user.id,
            token_hash=hash_chat_token(session_token),
            issued_at=now,
            expires_at=expires_at,
        )
        await self._sessions.add(row)
        _logger.info(
            "chat_session_created",
            extra={
                "fields": {
                    "session_id": str(row.id),
                    "tenant_id": row.tenant_id,
                    "platform_user_id": str(user.id),
                    "expires_at": expires_at.isoformat(),
                }
            },
        )
        return ChatSessionGrant(
            info=_info(
                tenant_id=row.tenant_id,
                user_id=user.id,
                display_name=user.display_name,
                session_id=row.id,
                expires_at=expires_at,
            ),
            session_token=SecretStr(session_token),
            idle_ttl_sec=idle_ttl_sec,
        )

    async def _session_ttl(self, tenant_id: str) -> timedelta:
        """交换期限：当前设置版本的 `webchat.session_ttl_hours`（缺省即 schema 默认 12 小时）。

        设置源损坏显式失败（503），不静默回落默认值；读取发生在行锁之后是为了让凭据错误（401）
        优先于基础设施错误，且这是一次同库索引 SELECT，不引入网络往返。
        """
        try:
            snapshot = await PlatformSettingsService(self._session).read_current(tenant_id)
        except PlatformSettingsError as exc:
            raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE) from exc
        return timedelta(hours=snapshot.settings.webchat.session_ttl_hours)

    async def _pg_now(self) -> datetime:
        """期限裁决基准：PG 服务端时间（同一事务内固定，跨实例不依赖机器墙钟）。"""
        value = await self._session.scalar(select(sa.func.now()))
        if not isinstance(value, datetime):
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return value


def _info(
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    display_name: str,
    session_id: uuid.UUID,
    expires_at: datetime,
) -> WebchatSessionInfo:
    """公开会话信息投影：`auth_context_id` 即本会话标识，供 X-Chat-Context 防误用。"""
    return WebchatSessionInfo(
        tenant_id=tenant_id,
        user_id=user_id,
        display_name=display_name,
        session_id=session_id,
        expires_at=expires_at,
        auth_context_id=session_id,
        auth_method=AUTH_METHOD_USER_TOKEN,
    )
