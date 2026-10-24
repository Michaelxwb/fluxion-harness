"""CHAT 公开认证路由（design §3.4 API-04；TASK-042）。

三条路由都挂在**管理登录守卫之外**（`api/router.py` 直接 `include_router`，不进 `authenticated`/
`admin` 组）：交换/恢复/登出只认 CHAT 凭据，不要求 Console 会话，也不授予任何 Console 权限。

- **exchange**：只接受 `WebchatTokenExchange.token`（`SecretStr`，契约层拒绝自报 user_id/tenant），
  身份从 PG Token 记录导出；`Depends(require_chat_origin)` 先于凭据与请求体判定。成功后按**库内
  剩余期限**写 CHAT 两枚 Cookie（`chat_cookie_max_age`，PG 服务端时间），响应 `no-store`。
- **me**：独立按 CHAT 摘要解析当前身份（`ChatSessionService.resolve`），返回统一 principal 七字段；
  安全方法免 CSRF，不滑动续期（零 `Set-Cookie`）、不读平台设置。
- **logout**：写请求先过独立 CHAT CSRF（`Depends(require_chat_csrf)`，Console 的 CSRF 对无效）；
  只撤本会话（幂等、可重放）、只清 CHAT 两枚 Cookie；不撤销来源 Token、不动管理会话 Cookie。

明文卫生：用户 Token 与 CHAT 会话原文只出现在请求体与 HttpOnly Cookie 两种传输面，本模块不打印、
不审计、不回显任何凭据值。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_contracts.webchat import WebchatTokenExchange
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.chat_auth_service import ChatSessionService
from ..infrastructure.db import get_session
from ..metrics import CHAT_AUTH_METRIC, count_outcome
from .chat_security import (
    CHAT_SESSION_COOKIE,
    chat_cookie_max_age,
    clear_chat_cookies,
    mark_no_store,
    require_chat_csrf,
    require_chat_origin,
    set_chat_cookies,
)
from .security import new_csrf_token

#: 公开认证面（无 Console 会话）：`/api/v1/webchat/auth/*`。
public_router = APIRouter(prefix="/api/v1/webchat/auth", tags=["webchat-auth"])

Session = Annotated[AsyncSession, Depends(get_session)]


def _presented_session_token(request: Request) -> SecretStr:
    """只从独立 CHAT Cookie 取凭据；缺失即 401（不回显任何输入值）。"""
    token = request.cookies.get(CHAT_SESSION_COOKIE, "")
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED)
    return SecretStr(token)


@public_router.post("/exchange", dependencies=[Depends(require_chat_origin)])
async def exchange_chat_token(
    payload: WebchatTokenExchange,
    request: Request,
    response: Response,
    session: Session,
) -> ApiResponse[Any]:
    """API-04 交换：Origin 先于凭据/请求体；身份从 Token 记录导出，原文只进 HttpOnly Cookie。"""
    with count_outcome(CHAT_AUTH_METRIC, {"operation": "exchange"}):
        grant = await ChatSessionService(session).exchange(payload.token)
        max_age = await chat_cookie_max_age(session, grant.info.expires_at)
        # 先提交再下发会话 Cookie：请求级 session 的 teardown 在响应发出之后才提交，
        # 直接返回会让客户端拿到令牌后的第一个请求查不到会话行（E2E 创建会话偶发 401）。
        await session.commit()
        set_chat_cookies(response, grant.session_token, new_csrf_token(), max_age)
    mark_no_store(response)
    return ok(request.app.state.message_catalog, grant.info.model_dump(mode="json"))


@public_router.get("/me")
async def read_chat_identity(
    request: Request, response: Response, session: Session
) -> ApiResponse[Any]:
    """API-04 会话信息：按独立 CHAT 凭据实时解析；不滑动续期、不继承管理角色。"""
    with count_outcome(CHAT_AUTH_METRIC, {"operation": "me"}):
        info = await ChatSessionService(session).resolve(_presented_session_token(request))
    mark_no_store(response)
    return ok(request.app.state.message_catalog, info.model_dump(mode="json"))


@public_router.post("/logout", dependencies=[Depends(require_chat_csrf)])
async def logout_chat_session(
    request: Request, response: Response, session: Session
) -> ApiResponse[Any]:
    """API-04 登出：撤本会话（幂等）、清 CHAT 两枚 Cookie；来源 Token 与管理会话不受影响。"""
    with count_outcome(CHAT_AUTH_METRIC, {"operation": "logout"}):
        await ChatSessionService(session).logout(_presented_session_token(request))
        await session.commit()
        clear_chat_cookies(response)
    mark_no_store(response)
    return ok(request.app.state.message_catalog, {"logged_out": True})
