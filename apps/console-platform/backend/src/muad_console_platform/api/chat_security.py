"""CHAT 独立 Cookie、CSRF 与 Origin 校验（design §3.4 API-04；TASK-041）。

本模块是 CHAT 认证面（`/api/v1/webchat/auth/*`）的安全层，只做"传输与凭据形态"，不做业务分支：

- **独立**：CHAT 的 Cookie 与 CSRF 名称都不与 Console 管理会话共用（`muad_chat_session` /
  `muad_chat_csrf` vs `muad_session` / `muad_csrf`），因此 CHAT 凭据不能当 Console 凭据，
  Console 的 CSRF 对也不能授权 CHAT 写请求；属性沿用共享安全底座（`security.cookie_secure` /
  `security.SAFE_METHODS` / `security.CSRF_HEADER`），不另起一套判断。
- **`Max-Age` 与库内剩余期限同源**：`chat_cookie_max_age` 用 **PG 服务端时间**算权威会话行的
  剩余整秒（向下取整，Cookie 不会比行活得更久）；剩余 ≤ 0 显式 `CHAT_SESSION_EXPIRED`，
  不写一个"已过期"的 Cookie 掩盖事实。普通请求不滑动续期。
- **Origin**：首期同源。交换 POST 必须先过 `require_chat_origin`（异源/`null` → 403，判定先于
  凭据与请求体），缺 `Origin` 的非浏览器客户端没有 ambient 凭据面，按可放行处理。
- **no-store**：交换/会话信息/登出响应都含私有数据，显式 `Cache-Control: no-store`。

凭据卫生：会话原文以 `SecretStr` 进入本层、只写进 HttpOnly Cookie；本模块不打印、不审计、
不回显任何 Token 值。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from fastapi import Request, Response
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .security import (
    CSRF_HEADER,
    SAFE_METHODS,
    cookie_secure,
    csrf_token_matches,
    is_same_origin,
)

#: CHAT 会话 Cookie：名称与管理会话（`muad_session`）完全分离，互不认证。
CHAT_SESSION_COOKIE = "muad_chat_session"
#: CHAT CSRF Cookie：与 `muad_csrf` 分离，Console 的 CSRF 值不能授权 CHAT 写请求。
CHAT_CSRF_COOKIE = "muad_chat_csrf"
#: 私有/凭据响应的缓存策略：代理与浏览器都不留副本（design §3.4）。
NO_STORE_CACHE_CONTROL = "no-store"


def set_chat_cookies(
    response: Response, session_token: SecretStr, csrf_token: str, max_age_sec: int
) -> None:
    """写 CHAT 两枚 Cookie：会话 HttpOnly、CSRF 可读（双提交需要脚本读值）。"""
    secure = cookie_secure()
    response.set_cookie(
        CHAT_SESSION_COOKIE,
        session_token.get_secret_value(),
        max_age=max_age_sec,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        CHAT_CSRF_COOKIE,
        csrf_token,
        max_age=max_age_sec,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )


def clear_chat_cookies(response: Response) -> None:
    """登出只清 CHAT Cookie；Console 的管理会话 Cookie 不在本层触碰。"""
    secure = cookie_secure()
    response.delete_cookie(
        CHAT_SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=secure
    )
    response.delete_cookie(
        CHAT_CSRF_COOKIE, path="/", httponly=False, samesite="strict", secure=secure
    )


def mark_no_store(response: Response) -> None:
    """交换/会话信息/登出都是私有响应：显式 no-store，不依赖默认头。"""
    response.headers["Cache-Control"] = NO_STORE_CACHE_CONTROL


async def chat_cookie_max_age(session: AsyncSession, expires_at: datetime) -> int:
    """Cookie `Max-Age` 与库内剩余期限同源：按 PG 服务端时间算剩余整秒（向下取整）。

    向下取整保证 Cookie 不会比权威会话行活得更久；剩余 ≤ 0 显式失败，不静默写 0。
    """
    now = await session.scalar(select(sa.func.now()))
    if not isinstance(now, datetime):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    remaining = int((expires_at - now).total_seconds())
    if remaining <= 0:
        raise AppError(ErrorCode.CHAT_SESSION_EXPIRED)
    return remaining


async def require_chat_origin(request: Request) -> None:
    """交换 POST 的 Origin 门控（首期同源）：异源/`null` 一律 403，先于凭据与请求体判定。"""
    if not is_same_origin(request):
        raise AppError(ErrorCode.FORBIDDEN)


async def require_chat_csrf(request: Request) -> None:
    """CHAT 写请求的独立 CSRF 双提交：只认 `muad_chat_csrf`，不认 Console 的 `muad_csrf`。"""
    if request.method in SAFE_METHODS:
        return
    if not csrf_token_matches(request, CHAT_CSRF_COOKIE, CSRF_HEADER):
        raise AppError(ErrorCode.FORBIDDEN)
