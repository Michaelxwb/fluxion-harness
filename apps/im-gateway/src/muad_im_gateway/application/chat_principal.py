"""Gateway 侧 CHAT 主体门控与长流复核（design §3.4 API-12/API-19、§3.7；TASK-044）。

语义要点：

- **只经 Console 核验**：网关不持库、不解析凭据。CHAT 会话原文原样转发给 Console 的
  `POST /internal/channel/chat-principal`（API-12），主体、授权集合与权威到期时刻都只从响应导出；
  浏览器自报 user_id/tenant 在这里没有位置。
- **单租户部署边界**：部署租户由装配根注入并随 `X-Tenant-Id` 转发，响应主体的租户必须等于它，
  否则 403 —— 不把 Token 认证误实现成跨租户 Gateway（设计 §3.4）。
- **上下文比对**：`principal.session_id` 必须等于请求携带的 `X-Chat-Context`（防 Cookie 切用户后
  页面误用身份）。Console 侧已比对，本层再比对一次：两层都不放行，判定先于任何主体/授权使用。
- **长流复核**：SSE 长流按 `chat_auth_recheck_sec`（启动资源预算，设计 §3.7：独立定时复核、不得
  晚于会话 `expires_at`）**定时复核会话与来源 Token —— 空闲也复核**（不与模型增量绑定），撤销在
  复核边界内生效，最迟在 `expires_at` 停止；复核只在定时器到点发生，**不每 token 查控制面**。
  终止一律抛 `AppError`（错误码沿用 Console 口径），不静默收流：前端据 401/409 关闭流并回到
  Token 输入（前端设计 E-101）。
- **不授予 Console 权限**：本层主体只有统一 principal 四个字段与授权入口集合，没有角色/权限/
  Console 账号或会话字段；CHAT 凭据也不当 Console 凭据转发。
- **凭据卫生**：会话原文以 `SecretStr` 在本层传递，`repr`/日志/异常文本里都没有它。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.auth_cache import AuthCacheStore, AuthCacheUnavailable
from muad_common.session_store import (
    RedisSessionStore,
    SessionStoreUnavailable,
    hash_session_token,
)
from muad_contracts.webchat import (
    ChatAuthMethod,
    ChatAuthorizedAgent,
    ChatPrincipal,
    ExternalIdentityGrant,
    ExternalIdentityView,
    ExternalPrincipalResult,
)
from pydantic import SecretStr

from .console_client import ConsoleClientPort

logger = logging.getLogger(__name__)

#: CHAT 会话 Cookie：Console 签发（`api/chat_security.CHAT_SESSION_COOKIE`）、网关读取，同一枚
#: （同源浏览器只带一份）。名称是两端的线上契约，改动必须同时改 Console 侧。
CHAT_SESSION_COOKIE = "muad_chat_session"


def _utc_now() -> datetime:
    return datetime.now(UTC)


#: 断言被拒原因 → 错误码（Console 已判过，本层仅转出；缺省 403）。
_EXTERNAL_DENIAL_CODES = {
    "UNBOUND": ErrorCode.EXTERNAL_ASSERTION_UNBOUND,
    "NO_ENTRY_GRANT": ErrorCode.EXTERNAL_ASSERTION_NOT_GRANTED,
    "NO_AGENT_GRANT": ErrorCode.EXTERNAL_ASSERTION_NOT_GRANTED,
    # Agent 被停用目前没有独立错误码，与「无授权」同归可用性拒绝：对调用方而言
    # 都是「这个入口现在不能用」，区分原因由 `/identity` 的 `reason` 承担。
    "AGENT_DISABLED": ErrorCode.EXTERNAL_ASSERTION_NOT_GRANTED,
    "USER_DISABLED": ErrorCode.EXTERNAL_ASSERTION_USER_DISABLED,
}

#: 「身份本身不可用」的拒绝原因——只有这些在取主体阶段直接失败。
#: 入口级原因（NO_ENTRY_GRANT / NO_AGENT_GRANT / AGENT_DISABLED）不在此列：
#: 身份是好的，只是当前没有可用入口，交由具体资源操作逐次判定（见 `resolve_external`）。
_IDENTITY_LEVEL_DENIALS = frozenset({"UNBOUND", "USER_DISABLED"})


@dataclass(frozen=True, slots=True)
class ChatSessionCredentials:
    """一次 CHAT 请求携带的凭据：Cookie 原文 + `X-Chat-Context`。

    原文以 `SecretStr` 保存（`repr`/日志无明文）；长流复核要用它**原样重放**，故随请求上下文传递。
    """

    #: WebChat（USER_TOKEN）：Console 签发的会话 token 原文 + `X-Chat-Context`。
    session_token: SecretStr | None = None
    chat_context: UUID | None = None
    #: MSSWWEB（EXTERNAL_ASSERTION）：上游平台的 `soc-token` 断言原文；无会话上下文。
    assertion: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class ChatIdentity:
    """Console 核验导出的受信主体：统一 principal + 权威到期时刻 + 当前授权入口集合。"""

    principal: ChatPrincipal
    expires_at: datetime
    agents: tuple[ChatAuthorizedAgent, ...]
    #: 逐入口裁决（仅断言面有）：`/entries` 据此标 `allowed`/`reason`。
    #: `USER_TOKEN` 面为空元组——该面没有「入口级不可用」概念，不可用入口直接不出现。
    grants: tuple[ExternalIdentityGrant, ...] = ()


def _require_session_credentials(
    credentials: ChatSessionCredentials,
) -> tuple[SecretStr, UUID]:
    """USER_TOKEN 路径必须带会话 token + 上下文；断言凭据走这里说明分流错了。"""
    if credentials.session_token is None or credentials.chat_context is None:
        raise AppError(ErrorCode.UNAUTHORIZED)
    return credentials.session_token, credentials.chat_context


class ChatPrincipalGate:
    """CHAT 主体门控：把"浏览器带来的凭据"换成"Console 核验过的受信主体"。"""

    def __init__(
        self,
        console: ConsoleClientPort,
        *,
        tenant_id: str,
        session_store: RedisSessionStore | None = None,
        auth_cache: AuthCacheStore | None = None,
    ) -> None:
        self._console = console
        self._tenant_id = tenant_id
        self._session_store = session_store
        #: 外部断言的凭证层/权限层；未装配时每次都回源 Console 完整验签。
        self._auth_cache = auth_cache

    async def resolve_credentials(
        self,
        credentials: ChatSessionCredentials,
        *,
        platform_key: str | None = None,
        channel: str | None = None,
    ) -> ChatIdentity:
        """按凭据类型分流：有断言走外部断言面，否则走 WebChat 会话 token。"""
        if credentials.assertion is not None:
            if platform_key is None or channel is None:
                raise AppError(ErrorCode.EXTERNAL_PLATFORM_UNAVAILABLE)
            return await self.resolve_external(
                credentials.assertion, platform_key=platform_key, channel=channel
            )
        return await self.resolve(credentials)

    async def resolve(self, credentials: ChatSessionCredentials) -> ChatIdentity:
        """核验并导出主体；跨部署租户 403、上下文不匹配 409，会话/Token 失效沿用 Console 错误码。"""
        token, context = _require_session_credentials(credentials)
        if self._session_store is not None:
            return await self._resolve_from_session_store(credentials)
        started = time.monotonic()
        response = await self._console.chat_principal(
            token,
            tenant_id=self._tenant_id,
            chat_context=context,
        )
        elapsed = time.monotonic() - started
        if elapsed > 1.0:
            logger.warning(
                "chat_principal_slow",
                extra={"tenant_id": self._tenant_id, "elapsed_ms": int(elapsed * 1000)},
            )
        principal = response.principal
        if principal.tenant_id != self._tenant_id:
            raise AppError(ErrorCode.FORBIDDEN)
        if principal.session_id != context:
            raise AppError(ErrorCode.AUTH_CONTEXT_CHANGED)
        return ChatIdentity(
            principal=principal,
            expires_at=response.expires_at,
            agents=tuple(response.agents),
        )

    async def _resolve_from_session_store(self, credentials: ChatSessionCredentials) -> ChatIdentity:
        """Redis 命中才放行；miss/Redis 故障一律重新认证，不回源 Console。"""
        token, context = _require_session_credentials(credentials)
        token_hash = hash_session_token(token.get_secret_value())
        try:
            record = await self._session_store.read(token_hash)  # type: ignore[union-attr]
        except SessionStoreUnavailable as exc:
            raise AppError(ErrorCode.UNAUTHORIZED) from exc
        if record is None:
            raise AppError(ErrorCode.UNAUTHORIZED)
        if record.tenant_id != self._tenant_id:
            raise AppError(ErrorCode.FORBIDDEN)
        if record.session_id != context:
            raise AppError(ErrorCode.AUTH_CONTEXT_CHANGED)
        try:
            alive = await self._session_store.touch(  # type: ignore[union-attr]
                token_hash, record, now=_utc_now()
            )
        except SessionStoreUnavailable as exc:
            raise AppError(ErrorCode.UNAUTHORIZED) from exc
        if not alive:
            raise AppError(ErrorCode.CHAT_SESSION_EXPIRED)
        if record.auth_method != "USER_TOKEN":
            # 会话记录由 Console 写入；未知认证方式说明共享存储被写坏，拒绝而不是猜。
            raise AppError(ErrorCode.UNAUTHORIZED)
        return ChatIdentity(
            principal=ChatPrincipal(
                tenant_id=record.tenant_id,
                platform_user_id=record.platform_user_id,
                session_id=record.session_id,
                auth_method=cast(ChatAuthMethod, record.auth_method),
            ),
            expires_at=record.expires_at,
            agents=tuple(
                ChatAuthorizedAgent(agent_id=agent.agent_id, key=agent.key, name=agent.name)
                for agent in record.agents
            ),
        )

    async def resolve_external(
        self, assertion: SecretStr, *, platform_key: str, channel: str
    ) -> ChatIdentity:
        """核验外部平台断言并导出主体（MSSWWEB / EXTERNAL_ASSERTION，设计 API-16）。

        只把断言原文转发给 Console（验签只在 Console，RULE-02）；本层再核一次跨部署租户。
        `allowed=false`（未绑定/无入口授权/用户停用/Agent 停用）按原因转 401/403，不把断言
        送进错误文本。

        **判定与 `/identity` 同源**：`allowed` 由 Console 的 `resolve_principal` 统一给出，
        本层不做二次放宽。这意味着 Agent 被停用后，旧授权**不再**能放行——这是有意的收口，
        详见 `resolve_principal` 的 docstring。
        """
        cached = await self._try_cached_external(assertion, platform_key=platform_key)
        result = (
            cached
            if cached is not None
            else await self._console.external_principal(
                assertion,
                tenant_id=self._tenant_id,
                platform_key=platform_key,
                channel=channel,
            )
        )
        # 「身份级不可用」才拦在这里：查无此人（UNBOUND）、用户停用（USER_DISABLED）。
        #
        # 「入口级不可用」（NO_ENTRY_GRANT / NO_AGENT_GRANT / AGENT_DISABLED）**不**在这层拦：
        # 身份是有效的，只是「此刻没有可用入口」。若在此拒绝，`/entries` 会变成 403 而不是
        # 空列表，前端会误判为「会话失效」去引导重新绑定——方向反了。入口级判定交给
        # 具体资源操作（提交消息等），`/entries` 则据 `agents` 集合自然过滤成空列表。
        if not result.allowed and result.reason in _IDENTITY_LEVEL_DENIALS:
            raise AppError(_EXTERNAL_DENIAL_CODES.get(result.reason, ErrorCode.FORBIDDEN))
        principal = result.principal
        if principal is None or result.expires_at is None:  # 契约已保证，防御式兜底
            raise AppError(ErrorCode.EXTERNAL_ASSERTION_INVALID)
        if principal.tenant_id != self._tenant_id:
            raise AppError(ErrorCode.FORBIDDEN)
        return ChatIdentity(
            principal=principal,
            expires_at=result.expires_at,
            agents=tuple(result.agents),
            grants=tuple(result.grants),
        )

    async def _try_cached_external(
        self, assertion: SecretStr, *, platform_key: str
    ) -> ExternalPrincipalResult | None:
        """凭证层命中 → 读权限层 → 构造主体。任一环节 miss / Redis 故障都返回 ``None``
        （调用方回源 Console 完整验签），不 fail closed——可用性优先。

        **凭证命中不代表权限可信**：授权只从权限层读，权限层被 `invalidate` 删除后必然
        回源，因此撤权即时生效。凭证无关的授权窗口由设计 §3.1.2 的「已知取舍」界定。
        """
        cache = self._auth_cache
        if cache is None:
            return None
        token_hash = hash_session_token(assertion.get_secret_value())
        now = _utc_now()
        try:
            raw = await cache.read_credential_plain(
                self._tenant_id, platform_key, token_hash
            )
        except AuthCacheUnavailable:
            return None
        if raw is None:
            return None
        try:
            record = json.loads(raw)
            expires_at_epoch = float(record["expires_at_epoch"])
            platform_user_id = UUID(record["platform_user_id"])
            session_id = UUID(record["session_id"])
        except (ValueError, KeyError, TypeError):
            # 缓存内容损坏：当作 miss，回源重建。
            return None
        if now.timestamp() >= expires_at_epoch:
            return None
        try:
            alive = await cache.touch_credential(
                self._tenant_id,
                platform_key,
                token_hash,
                expires_at_epoch=expires_at_epoch,
                now_epoch=now.timestamp(),
            )
        except AuthCacheUnavailable:
            return None
        if not alive:
            return None
        permission = await self._read_cached_permission(
            cache, platform_user_id, session_id=session_id
        )
        if permission is None:
            return None
        return permission

    async def _read_cached_permission(
        self,
        cache: AuthCacheStore,
        platform_user_id: UUID,
        *,
        session_id: UUID,
    ) -> ExternalPrincipalResult | None:
        """读权限层并还原成 `ExternalPrincipalResult`。

        还原后交由调用方走**与回源完全相同**的判定，因此 `allowed=false` 的缓存结论
        （含 USER_DISABLED 等身份级拒绝）不会被当成放行——权限层缓存的是结论，不是豁免。
        miss / 损坏 / Redis 故障都返回 ``None``（回源）。
        """
        try:
            raw = await cache.read_permission_plain(self._tenant_id, platform_user_id)
        except AuthCacheUnavailable:
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        try:
            result = ExternalPrincipalResult.model_validate(
                {
                    "allowed": data["allowed"],
                    "reason": data.get("reason"),
                    "principal": {
                        "tenant_id": self._tenant_id,
                        "platform_user_id": str(platform_user_id),
                        "session_id": str(session_id),
                        "auth_method": "EXTERNAL_ASSERTION",
                    },
                    "agents": data["agents"],
                    "grants": data["grants"],
                    "expires_at": data["expires_at"],
                }
            )
        except (KeyError, TypeError, ValueError):
            return None
        try:
            await cache.touch_permission(self._tenant_id, platform_user_id)
        except AuthCacheUnavailable:
            # 续期失败不影响本次结论；下次命中自然回源。
            logger.warning("external_permission_touch_failed")
        return result

    async def describe_external(
        self,
        assertion: SecretStr,
        *,
        platform_key: str,
        channel: str,
        entry_id: UUID | None = None,
    ) -> ExternalIdentityView:
        """权限查询投影（`GET /identity`，设计 API-01）。

        与 :meth:`resolve_external` 的区别是**不把拒绝变成异常**：权限查询的「不行」是正常
        结果，交给调用方以 200 + `allowed=false` 返回。只有「断言本身不可信」（401）、
        「平台未配置」（503）这两类才抛。
        """
        return await self._console.external_identity(
            assertion,
            tenant_id=self._tenant_id,
            platform_key=platform_key,
            channel=channel,
            entry_id=entry_id,
        )


class ChatStreamGuard:
    """长流（SSE）复核：空闲也定时复核、到 `expires_at` 即停、不每 token 查控制面。

    `recheck_sec` 来自启动资源预算 `chat_auth_recheck_sec`（设计 §3.7）：每次复核都经 Console
    重放原凭据，撤销/来源 Token 失效/用户停用/上下文变化都在此终止流。
    """

    def __init__(
        self,
        gate: ChatPrincipalGate,
        credentials: ChatSessionCredentials,
        identity: ChatIdentity,
        *,
        recheck_sec: float,
        platform_key: str | None = None,
        channel: str | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if recheck_sec <= 0:
            raise ValueError("recheck_sec must be positive")
        self._gate = gate
        self._credentials = credentials
        self._identity = identity
        self._recheck_sec = recheck_sec
        self._platform_key = platform_key
        self._channel = channel
        self._now = now

    async def rechecked[ItemT](self, events: AsyncIterator[ItemT]) -> AsyncIterator[ItemT]:
        """事件流 + 定时复核：事件原样转发，复核按**周期**发生（事件到达不推迟复核、洪峰也不
        触发按 token 的控制面查询）。"""
        iterator = events.__aiter__()
        pending: asyncio.Task[ItemT] = asyncio.ensure_future(anext(iterator))
        next_recheck_at = self._now() + timedelta(seconds=self._recheck_sec)
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=self._wait_sec(next_recheck_at))
                if not done:
                    await self._recheck()
                    next_recheck_at = self._now() + timedelta(seconds=self._recheck_sec)
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                pending = asyncio.ensure_future(anext(iterator))
                yield event
        finally:
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending

    def _wait_sec(self, next_recheck_at: datetime) -> float:
        """下一次等待时长：**周期复核截止**与到 `expires_at` 的剩余时间取小（不为负）。

        复核截止一旦排定就只由到点推进：事件（心跳/增量）到达不会把它推后——否则事件比复核周期
        密时复核永远轮不上，撤销/到期只能等流自然结束（设计 §3.4：空闲也复核、不得只在收到模型
        增量时检查）。`expires_at` 是 Console 给出的权威到期时刻（TASK-043 交接），本地钟只用于
        **排程**这个停止点；到点即停，不越过会话期限继续等。
        """
        now = self._now()
        return max(
            0.0,
            min(
                (next_recheck_at - now).total_seconds(),
                (self._identity.expires_at - now).total_seconds(),
            ),
        )

    async def _recheck(self) -> None:
        """到点复核：到期即停；否则经 Console 重放凭据（撤销/失效在此抛错终止流）。"""
        if self._now() >= self._identity.expires_at:
            raise AppError(ErrorCode.CHAT_SESSION_EXPIRED)
        self._identity = await self._gate.resolve_credentials(
            self._credentials, platform_key=self._platform_key, channel=self._channel
        )
