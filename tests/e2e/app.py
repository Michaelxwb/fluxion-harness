"""真实浏览器 E2E 的 Console 后端：api-kit 封套 + 会话原语 + 静态前端产物。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from muad_api import (
    ApiResponse,
    AppError,
    ErrorCode,
    install_api_foundation,
    install_console_security,
    install_health_probes,
    ok,
    require_session,
)
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps/console-platform/frontend/dist"
MESSAGES_FILE = ROOT / "config/api-messages.yaml"

SESSION_COOKIE = "muad_session"
E2E_SESSION_TOKEN = "e2e-session"
E2E_ACCOUNT = {
    "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "e2e-admin")),
    "username": "admin",
    "display_name": "E2E Admin",
    "role": "ADMIN",
}


class LoginPayload(BaseModel):
    username: str
    password: str


class SessionVerifier:
    async def verify(self, session_token: str) -> dict[str, str] | None:
        return dict(E2E_ACCOUNT) if session_token == E2E_SESSION_TOKEN else None


class RoleResolver:
    async def roles_for(self, principal: dict[str, str]) -> set[str]:
        return {principal["role"]}


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
catalog = install_api_foundation(app, messages_file=MESSAGES_FILE, default_locale="zh-CN")
install_console_security(app, SessionVerifier(), RoleResolver())
install_health_probes(app, {})


@app.post("/api/v1/auth/login")
async def login(payload: LoginPayload, response: Response) -> ApiResponse[Any]:
    if payload.username != "admin" or payload.password != "admin123":
        raise AppError(ErrorCode.UNAUTHORIZED)
    response.set_cookie(SESSION_COOKIE, E2E_SESSION_TOKEN, httponly=True, samesite="lax", path="/")
    return ok(catalog, dict(E2E_ACCOUNT))


@app.post("/api/v1/auth/logout")
async def logout(response: Response) -> ApiResponse[Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok(catalog, {"logged_out": True})


SessionPrincipal = Annotated[dict[str, Any], Depends(require_session)]


@app.get("/api/v1/auth/me")
async def me(principal: SessionPrincipal) -> ApiResponse[Any]:
    return ok(catalog, principal)


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    candidate = (DIST / full_path).resolve()
    if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(DIST / "index.html")
