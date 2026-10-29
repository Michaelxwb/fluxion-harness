import hmac
import secrets

from fastapi import Request, Response
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import process_settings

SESSION_COOKIE = "muad_session"
CSRF_COOKIE = "muad_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
ORIGIN_HEADER = "Origin"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_secure() -> bool:
    """Cookie `Secure` 策略的唯一来源：仅非 dev 打开（dev 走本地 http 调试）。

    Console 与 CHAT 的 Cookie 都从这里取属性，不各自判断环境。
    """
    return process_settings().env != "dev"


def csrf_token_matches(request: Request, cookie_name: str, header_name: str) -> bool:
    """CSRF 双提交比对：Cookie 与头必须同时存在且逐字节相等（`hmac.compare_digest`）。"""
    cookie_token = request.cookies.get(cookie_name, "")
    header_token = request.headers.get(header_name, "")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


def request_origin(request: Request) -> str:
    """请求自身的来源（`scheme://host`）：Host 由浏览器/代理写入，页面脚本不能改写。"""
    url = request.url
    return f"{url.scheme}://{url.netloc}".lower()


def is_same_origin(request: Request) -> bool:
    """首期同源判定：缺 `Origin` 视为非浏览器客户端（无 ambient 凭据面）；`null`/异源不同源。"""
    origin = request.headers.get(ORIGIN_HEADER)
    if origin is None:
        return True
    return origin.rstrip("/").lower() == request_origin(request)


def set_auth_cookies(response: Response, session_token: str, csrf_token: str, max_age_sec: int) -> None:
    """写认证 cookie；`max_age_sec` 由调用方传入，须与会话行的 TTL **同源**。"""
    secure = cookie_secure()
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age_sec,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age_sec,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    secure = cookie_secure()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=secure)
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=False, samesite="strict", secure=secure)


async def require_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    if not csrf_token_matches(request, CSRF_COOKIE, CSRF_HEADER):
        raise AppError(ErrorCode.FORBIDDEN)
