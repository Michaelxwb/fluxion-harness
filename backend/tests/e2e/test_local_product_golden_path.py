from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from tests.console_helpers import runtime_profile_spec, tenant_headers

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.runtime_app import RuntimeApplicationService


@pytest.mark.asyncio
async def test_S_R01_local_console_sqlite_runtime_and_web_chat_golden_path(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fluxion-product.db"
    store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{database}")
    runtime = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    console = ConsoleApplicationService(store)
    channel = ChannelApplicationService(store, runtime, code_factory=lambda: "LOCAL-BIND-CODE")
    await store.initialize()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_console_app(console)),
            base_url="http://console",
        ) as console_client:
            created = await console_client.post(
                "/api/v1/resources/runtime_profile",
                json={
                    "tenant_id": "tenant-a",
                    "resource_id": "assistant",
                    "version": "1",
                    "visibility": "private",
                    "spec": runtime_profile_spec(),
                },
                headers=tenant_headers(),
            )
            published = await console_client.post(
                "/api/v1/resources/runtime_profile/assistant/versions/1:publish",
                json={"expected_base_version": "1"},
                headers=tenant_headers(request_id="req-publish"),
            )
            # TASK-A104：persona/model 在同名 AgentDefinition（golden path 数据面）。
            from tests.runtime_helpers import seed_agent_definition
            await seed_agent_definition(store, provider_id="dev.echo", system_prompt="你是 Fluxion 产品助手。")
        await channel.create_platform_user("tenant-a", "user-a", display_name="用户 A")
        issued = await channel.issue_bind_code("tenant-a", "user-a")

        async with AsyncClient(
            transport=ASGITransport(app=create_channel_app(channel)),
            base_url="http://chat",
        ) as chat_client:
            bound = await chat_client.post(
                "/api/v1/channels/web/messages",
                json=_chat_payload(f"/bind {issued.code}", "bind"),
                headers=tenant_headers(actor_id="browser-a", request_id="req-bind"),
            )
            streamed = await chat_client.post(
                "/api/v1/channels/web/messages:stream",
                json=_chat_payload("hello product", "message"),
                headers=tenant_headers(actor_id="browser-a", request_id="req-chat"),
            )

        assert created.status_code == 200
        assert published.status_code == 200
        assert bound.status_code == 200
        assert bound.json()["data"]["platform_user_id"] == "user-a"
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert 'event: completed' in streamed.text
        assert '"output": "dev: hello product"' in streamed.text  # 模型名归 MODEL 链
        assert database.exists()
    finally:
        await store.close()


def _chat_payload(content: str, message_id: str) -> dict[str, str]:
    return {
        "channel_user_id": "browser-a",
        "conversation_id": "conversation-a",
        "message_id": message_id,
        "content": content,
        "agent_id": "assistant",
    }
