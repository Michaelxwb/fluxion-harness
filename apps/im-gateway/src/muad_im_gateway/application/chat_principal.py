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
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.webchat import ChatAuthorizedAgent, ChatPrincipal
from pydantic import SecretStr

from .console_client import ConsoleClientPort

logger = logging.getLogger(__name__)

#: CHAT 会话 Cookie：Console 签发（`api/chat_security.CHAT_SESSION_COOKIE`）、网关读取，同一枚
#: （同源浏览器只带一份）。名称是两端的线上契约，改动必须同时改 Console 侧。
CHAT_SESSION_COOKIE = "muad_chat_session"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ChatSessionCredentials:
    """一次 CHAT 请求携带的凭据：Cookie 原文 + `X-Chat-Context`。

    原文以 `SecretStr` 保存（`repr`/日志无明文）；长流复核要用它**原样重放**，故随请求上下文传递。
    """

    session_token: SecretStr
    chat_context: UUID


@dataclass(frozen=True, slots=True)
class ChatIdentity:
    """Console 核验导出的受信主体：统一 principal + 权威到期时刻 + 当前授权入口集合。"""

    principal: ChatPrincipal
    expires_at: datetime
    agents: tuple[ChatAuthorizedAgent, ...]


class ChatPrincipalGate:
    """CHAT 主体门控：把"浏览器带来的凭据"换成"Console 核验过的受信主体"。"""

    def __init__(self, console: ConsoleClientPort, *, tenant_id: str) -> None:
        self._console = console
        self._tenant_id = tenant_id

    async def resolve(self, credentials: ChatSessionCredentials) -> ChatIdentity:
        """核验并导出主体；跨部署租户 403、上下文不匹配 409，会话/Token 失效沿用 Console 错误码。"""
        started = time.monotonic()
        response = await self._console.chat_principal(
            credentials.session_token,
            tenant_id=self._tenant_id,
            chat_context=credentials.chat_context,
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
        if principal.session_id != credentials.chat_context:
            raise AppError(ErrorCode.AUTH_CONTEXT_CHANGED)
        return ChatIdentity(
            principal=principal,
            expires_at=response.expires_at,
            agents=tuple(response.agents),
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
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if recheck_sec <= 0:
            raise ValueError("recheck_sec must be positive")
        self._gate = gate
        self._credentials = credentials
        self._identity = identity
        self._recheck_sec = recheck_sec
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
        self._identity = await self._gate.resolve(self._credentials)
