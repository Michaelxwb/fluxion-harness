"""CMD-02 / CMD-03（integration）：Command Plane 管道收口验收。

- CMD-02：已绑定用户发未知 slash → `unknown_command`，且 Runtime 零调用
  （未知命令 fail-closed，不进 LLM；`//` 转义进 Runtime 原文）。
- CMD-03：`/bind` 迁移后行为不变（成功仍为 kind="bound"）；已绑定再 bind →
  `already_bound`；`/help` 由 Registry 自动生成并按上下文过滤。

先写测试记 RED：`fluxion.commands` 与管道收口在 TASK-002 实现前不存在。
"""

from __future__ import annotations

import pytest

from fluxion.plugins.channel_adapters import StubImChannelAdapter, WebChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from tests.channel_helpers import RecordingRuntime, verified_identity
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


async def _store() -> PostgreSQLRegistryStore:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    return store


async def _publish_assistant(store: PostgreSQLRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")


def _im_message(
    channel_user_id: str, content: str, message_id: str
) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conv-{channel_user_id}",
        message_id=message_id,
        content=content,
        agent_id="assistant",
    )


async def _bind_im(
    service: ChannelApplicationService, channel_user_id: str, code: str
) -> None:
    result = await service.handle(
        StubImChannelAdapter(),
        _im_message(channel_user_id, f"/bind {code}", f"m-bind-{channel_user_id}"),
    verified=None,
    )
    assert result.kind == "bound"


@pytest.mark.asyncio
async def test_CMD02_unknown_slash_never_reaches_runtime() -> None:
    store = await _store()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    try:
        await service.create_platform_user("tenant-a", "user-a")
        await _publish_assistant(store)
        issued = await service.issue_bind_code("tenant-a", "user-a")
        await _bind_im(service, "im-1", issued.code)

        result = await service.handle(
            StubImChannelAdapter(), _im_message("im-1", "/foo", "m-unknown"),
        verified=verified_identity("im-1"),
        )
        payload = result.to_payload()
        assert payload["kind"] == "command"
        assert payload["command"] == "foo"
        assert payload["code"] == "unknown_command"
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD02_double_slash_enters_runtime_as_literal() -> None:
    store = await _store()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    try:
        await service.create_platform_user("tenant-a", "user-a")
        await _publish_assistant(store)
        issued = await service.issue_bind_code("tenant-a", "user-a")
        await _bind_im(service, "im-1", issued.code)

        result = await service.handle(
            StubImChannelAdapter(), _im_message("im-1", "//new", "m-literal"),
        verified=verified_identity("im-1"),
        )
        assert result.kind == "message"
        assert len(runtime.requests) == 1
        assert runtime.requests[0].input_message == "/new"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD02_uppercase_command_is_unknown() -> None:
    store = await _store()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    try:
        await service.create_platform_user("tenant-a", "user-a")
        await _publish_assistant(store)
        issued = await service.issue_bind_code("tenant-a", "user-a")
        await _bind_im(service, "im-1", issued.code)

        result = await service.handle(
            StubImChannelAdapter(), _im_message("im-1", "/NEW", "m-upper"),
        verified=verified_identity("im-1"),
        )
        assert result.to_payload()["code"] == "unknown_command"
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD03_bind_migration_keeps_bound_kind() -> None:
    store = await _store()
    service = ChannelApplicationService(store, RecordingRuntime())
    try:
        await service.create_platform_user("tenant-a", "user-a")
        issued = await service.issue_bind_code("tenant-a", "user-a")
        result = await service.handle(
            StubImChannelAdapter(),
            _im_message("im-9", f"/bind {issued.code}", "m-bind-9"),
        verified=None,
        )
        assert result.kind == "bound"
        assert result.platform_user_id == "user-a"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD03_rebind_returns_already_bound() -> None:
    store = await _store()
    service = ChannelApplicationService(store, RecordingRuntime())
    try:
        await service.create_platform_user("tenant-a", "user-a")
        await _publish_assistant(store)
        issued = await service.issue_bind_code("tenant-a", "user-a")
        await _bind_im(service, "im-1", issued.code)

        result = await service.handle(
            StubImChannelAdapter(), _im_message("im-1", "/bind whatever", "m-rebind"),
        verified=verified_identity("im-1"),
        )
        payload = result.to_payload()
        assert payload["kind"] == "command"
        assert payload["code"] == "already_bound"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD03_help_generated_from_registry_and_filtered() -> None:
    store = await _store()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    try:
        await service.create_platform_user("tenant-a", "user-a")
        await _publish_assistant(store)
        issued = await service.issue_bind_code("tenant-a", "user-a")
        await _bind_im(service, "im-1", issued.code)

        result = await service.handle(
            StubImChannelAdapter(), _im_message("im-1", "/help", "m-help"),
        verified=verified_identity("im-1"),
        )
        payload = result.to_payload()
        assert payload["kind"] == "command"
        assert payload["command"] == "help"
        assert payload["code"] == "ok"
        assert "/help" in payload["output"]
        # Web/已绑定上下文不得展示 /bind
        assert "/bind" not in payload["output"]
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_CMD03_unbound_plain_text_keeps_unbound_hint() -> None:
    store = await _store()
    service = ChannelApplicationService(store, RecordingRuntime())
    try:
        result = await service.handle(
            WebChannelAdapter(), _im_message("ghost", "hello", "m-ghost"),
        verified=verified_identity("ghost"),
        )
        assert result.kind == "unbound"
        assert "/bind" in result.output
    finally:
        await store.close()
