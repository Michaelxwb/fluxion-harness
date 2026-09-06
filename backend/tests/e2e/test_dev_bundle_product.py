from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.dev_bundle import create_dev_bundle_app
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


@pytest.mark.asyncio
async def test_S_P13_06_dev_bundle_routes_static_console_chat_and_shared_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import os
    import uuid

    # ADR-A007：dev bundle 要求显式 master key。
    monkeypatch.setenv(
        "FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    # 共享 PG 跨 run 隔离：固定租户 dev 下用户唯一。
    user_id = f"user-a-{uuid.uuid4().hex[:8]}"
    console_dist = _static_app(tmp_path / "console", "console-product")
    chat_dist = _static_app(tmp_path / "chat", "chat-product")
    app = create_dev_bundle_app(
        registry_dsn=TEST_POSTGRES_DSN,
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
            json={"platform_user_id": user_id, "display_name": "User A"},
        )
        # bundle 内部自建 store：按同 DSN 开句柄补种同名 Agent（TASK-A105 校验前置）。
        from fluxion.registry import PostgreSQLRegistryStore as _SRS
        _seed_store = _SRS(TEST_POSTGRES_DSN)
        await _seed_store.initialize()
        await seed_agent_definition(_seed_store, tenant_id="dev", system_prompt="你是测试代理。")
        await _seed_store.close()
        issued = await client.post(
            f"/api/v1/platform-users/{user_id}/chat-access",
            json={"agent_id": "assistant"},
        )
        token = issued.json()["data"]["token"]
        resolved = await client.get(
            "/api/v1/channels/web/access",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert console.text == "console-product"
    assert chat.text == "chat-product"
    assert created.json()["data"]["tenant_id"] == "dev"
    assert resolved.json()["data"]["platform_user_id"] == user_id


def _static_app(path: Path, body: str) -> Path:
    path.mkdir()
    (path / "index.html").write_text(body, encoding="utf-8")
    return path
