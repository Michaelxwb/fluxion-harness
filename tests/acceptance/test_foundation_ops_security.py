from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from muad_api import install_api_foundation, install_console_security, require_roles, require_session

ROOT = Path(__file__).resolve().parents[2]
MESSAGES_FILE = ROOT / "config/api-messages.yaml"

Principal = dict[str, Any]
AdminUser = Annotated[Principal, Depends(require_roles("admin"))]
SuperUser = Annotated[Principal, Depends(require_roles("superuser"))]
SessionUser = Annotated[Principal, Depends(require_session)]


class StubSessionVerifier:
    async def verify(self, session_token: str) -> Principal | None:
        if session_token == "good-token":
            return {"id": "u-1001"}
        return None


class StubRoleResolver:
    async def roles_for(self, principal: Principal) -> set[str]:
        return {"admin"}


def _build_client() -> TestClient:
    app = FastAPI()
    install_api_foundation(app, messages_file=MESSAGES_FILE, default_locale="zh-CN")
    install_console_security(app, StubSessionVerifier(), StubRoleResolver())

    @app.get("/admin")
    async def admin(principal: AdminUser) -> Principal:
        return {"id": principal["id"]}

    @app.get("/superuser")
    async def superuser(_: SuperUser) -> Principal:
        return {"ok": True}

    @app.get("/session")
    async def session(principal: SessionUser) -> Principal:
        return {"id": principal["id"]}

    return TestClient(app)


def test_s10_missing_session_is_unauthorized() -> None:
    client = _build_client()
    for path in ("/admin", "/session"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"


def test_s10_insufficient_role_is_forbidden() -> None:
    client = _build_client()
    response = client.get("/superuser", headers={"Authorization": "Bearer good-token"})
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_s10_valid_session_passes_cookie_and_bearer() -> None:
    client = _build_client()
    bearer = client.get("/admin", headers={"Authorization": "Bearer good-token"})
    assert bearer.status_code == 200
    assert bearer.json() == {"id": "u-1001"}

    client.cookies.set("muad_session", "good-token")
    cookie = client.get("/session")
    assert cookie.status_code == 200
    assert cookie.json() == {"id": "u-1001"}

    client.cookies.set("muad_session", "expired-token")
    assert client.get("/session").status_code == 401
