from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.dev_bundle import create_dev_bundle_app


@pytest.mark.asyncio
async def test_S_P13_06_dev_bundle_routes_static_console_chat_and_shared_api(
    tmp_path: Path,
) -> None:
    console_dist = _static_app(tmp_path / "console", "console-product")
    chat_dist = _static_app(tmp_path / "chat", "chat-product")
    app = create_dev_bundle_app(
        registry_dsn=f"sqlite+aiosqlite:///{tmp_path / 'bundle.db'}",
        console_dist=console_dist,
        chat_dist=chat_dist,
    )

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://bundle",
    ) as client:
        console = await client.get("/console/")
        chat = await client.get("/chat/")
        created = await client.post(
            "/api/v1/platform-users",
            json={"platform_user_id": "user-a", "display_name": "User A"},
        )
        issued = await client.post(
            "/api/v1/platform-users/user-a/chat-access",
            json={"runtime_profile_id": "assistant"},
        )
        token = issued.json()["data"]["token"]
        resolved = await client.get(
            "/api/v1/channels/web/access",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert console.text == "console-product"
    assert chat.text == "chat-product"
    assert created.json()["data"]["tenant_id"] == "dev"
    assert resolved.json()["data"]["platform_user_id"] == "user-a"


def _static_app(path: Path, body: str) -> Path:
    path.mkdir()
    (path / "index.html").write_text(body, encoding="utf-8")
    return path
