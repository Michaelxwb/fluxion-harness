from __future__ import annotations

import pytest
from tests.channel_helpers import RecordingRuntime

from fluxion.plugins.channel_adapters import WebChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.channel_app import ChannelApplicationService


@pytest.mark.asyncio
async def test_S_C105_channel_identity_maps_to_platform_user_store() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime, code_factory=lambda: "BIND-S-C105")
    await store.initialize()
    try:
        await service.create_platform_user("tenant-a", "user-a", display_name="用户 A")
        issued = await service.issue_bind_code("tenant-a", "user-a")

        result = await service.handle(
            WebChannelAdapter(),
            ExternalChannelMessage(
                tenant_id="tenant-a",
                channel_user_id="browser-a",
                conversation_id="conversation-a",
                message_id="message-bind",
                content=f"/bind {issued.code}",
                runtime_profile_id="assistant",
            ),
        )
        identity = await service.resolve_identity("tenant-a", "web", "browser-a")

        assert result.kind == "bound"
        assert result.platform_user_id == "user-a"
        assert identity is not None
        assert identity.platform_user_id == "user-a"
        assert identity.tenant_id == "tenant-a"
    finally:
        await store.close()
