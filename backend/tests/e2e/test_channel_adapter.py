from __future__ import annotations

import pytest
from tests.channel_helpers import RecordingRuntime
from tests.runtime_helpers import seed_runtime_profile, TEST_POSTGRES_DSN

from fluxion.plugins.channel_adapters import StubImChannelAdapter, WebChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.channel_app import ChannelApplicationService


@pytest.mark.asyncio
async def test_S_C119_web_and_stub_im_share_channel_contract_and_runtime() -> None:
    codes = iter(("WEB-CODE", "IM-CODE"))
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime, code_factory=lambda: next(codes))
    await store.initialize()
    # ADR-A010：执行主坐标是 AgentDefinition（agent_id=assistant），
    # 需 seed 同名 agent + 租户 default profile（同名回退已删除）。
    await seed_runtime_profile(store)
    try:
        await service.create_platform_user("tenant-a", "user-a", display_name="用户 A")
        web_code = await service.issue_bind_code("tenant-a", "user-a")
        im_code = await service.issue_bind_code("tenant-a", "user-a")
        web = WebChannelAdapter()
        im = StubImChannelAdapter()

        await service.handle(web, _message("browser-a", f"/bind {web_code.code}", "bind-web"))
        await service.handle(im, _message("im-a", f"/bind {im_code.code}", "bind-im"))
        web_result = await service.handle(web, _message("browser-a", "hello", "web-message"))
        im_result = await service.handle(im, _message("im-a", "hello", "im-message"))

        assert web_result.output == im_result.output == "echo: hello"
        assert [request.user_id for request in runtime.requests] == ["user-a", "user-a"]
        assert [request.input_message for request in runtime.requests] == ["hello", "hello"]
        assert web.channel_type == "web"
        assert im.channel_type == "stub-im"
    finally:
        await store.close()


def _message(channel_user_id: str, content: str, message_id: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conversation-{channel_user_id}",
        message_id=message_id,
        content=content,
        agent_id="assistant",
    )
