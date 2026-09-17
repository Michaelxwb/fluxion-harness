import hmac
import secrets

from fastapi import Request, Response
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

SESSION_COOKIE = "muad_session"
CSRF_COOKIE = "muad_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
SESSION_MAX_AGE_SEC = 12 * 60 * 60


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_secure() -> bool:
    return SharedSettings().env != "dev"


def set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    secure = _cookie_secure()
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=SESSION_MAX_AGE_SEC,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    secure = _cookie_secure()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=secure)
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=False, samesite="strict", secure=secure)


async def require_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get(CSRF_HEADER, "")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise AppError(ErrorCode.FORBIDDEN)
