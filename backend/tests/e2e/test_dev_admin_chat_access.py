from __future__ import annotations

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.channel_helpers import RecordingRuntime

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.config import DevModeSettings
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import chat_access_tokens, platform_users
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService


@pytest.mark.asyncio
async def test_S_P13_04_fixed_admin_creates_user_and_resolvable_chat_link() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime = RecordingRuntime()
    settings = DevModeSettings(enabled=True)
    console = ConsoleApplicationService(store)
    channel = ChannelApplicationService(store, runtime)
    await store.initialize()
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=create_console_app(console, dev_mode=settings)),
                base_url="http://console",
            ) as console_client,
            AsyncClient(
                transport=ASGITransport(app=create_channel_app(channel, dev_mode=settings)),
                base_url="http://chat",
            ) as chat_client,
        ):
            created = await console_client.post(
                "/api/v1/platform-users",
                json={"platform_user_id": "user-a", "display_name": "用户 A"},
                headers={"X-Tenant-ID": "tenant-a", "X-Actor-ID": "admin-a"},
            )
            # TASK-A105：chat access 发行前置校验目标 agent 已发布（fixture 补种）。
            from tests.runtime_helpers import seed_agent_definition
            await seed_agent_definition(store, tenant_id="dev", provider_id="dev.echo")
            issued = await console_client.post(
                "/api/v1/platform-users/user-a/chat-access",
                json={"agent_id": "assistant"},
                headers={"X-Tenant-ID": "tenant-a", "X-Actor-ID": "admin-a"},
            )
            token = issued.json()["data"]["token"]
            resolved = await chat_client.get(
                "/api/v1/channels/web/access",
                headers={"Authorization": f"Bearer {token}"},
            )
            messaged = await chat_client.post(
                "/api/v1/channels/web/access/messages",
                json={
                    "conversation_id": "conversation-a",
                    "message_id": "message-a",
                    "content": "hello access",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert created.status_code == 200
        assert created.json()["data"]["tenant_id"] == "dev"
        assert issued.status_code == 200
        assert issued.json()["data"]["chat_path"] == f"/chat/#/{token}"
        assert resolved.status_code == 200
        assert resolved.json()["data"] == {
            "access_id": issued.json()["data"]["access_id"],
            "tenant_id": "dev",
            "platform_user_id": "user-a",
            "agent_id": "assistant",
        }
        assert messaged.status_code == 200
        assert messaged.json()["data"]["output"] == "echo: hello access"
        assert len(runtime.requests) == 1
        assert runtime.requests[0].tenant_id == "dev"
        assert runtime.requests[0].user_id == "user-a"
        assert runtime.requests[0].agent_definition_id == "assistant"

        async with store.engine.connect() as connection:
            user_row = (await connection.execute(select(platform_users))).mappings().one()
            access_row = (await connection.execute(select(chat_access_tokens))).mappings().one()
        assert user_row["tenant_id"] == "dev"
        assert access_row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in repr(dict(access_row))
    finally:
        await store.close()
