"""Apply committed session renewal to the final response, including file responses."""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ..application.auth_service import ResolvedConsoleSession
from .security import CSRF_COOKIE, SESSION_COOKIE, new_csrf_token, set_auth_cookies


class SessionRenewalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        principal = getattr(request.state, "principal", None)
        token = request.cookies.get(SESSION_COOKIE)
        bearer = request.headers.get("Authorization", "").lower().startswith("bearer ")
        if (
            isinstance(principal, ResolvedConsoleSession)
            and principal.renewed_ttl is not None
            and token
            and not bearer
            and request.url.path != "/api/v1/auth/logout"
            and response.status_code != 401
        ):
            set_auth_cookies(
                response, token, request.cookies.get(CSRF_COOKIE) or new_csrf_token(),
                int(principal.renewed_ttl.total_seconds()),
            )
        return response
