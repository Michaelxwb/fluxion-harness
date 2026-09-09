"""AUTH-01 / AUTH-02（integration）：handle() 已验证身份结构性保证。

- AUTH-01：verified=None（匿名）+ 已绑定 channel_user_id + 普通消息 →
  只走未绑定流（提示），Runtime 零调用。冒充在结构上不可能。
- AUTH-02：verified 身份与消息 channel_user_id 不一致 → ChannelAuthError。
- AUTH-03：S-06 双测试保持绿（见 test_web_message_auth.py）。

先写测试记 RED：`handle()` 尚无 verified 参数。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.channel_auth import ChannelAuthError, VerifiedChannelIdentity
from tests.channel_helpers import RecordingRuntime
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


async def _setup() -> tuple[PostgreSQLRegistryStore, RecordingRuntime, ChannelApplicationService]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    await service.create_platform_user("tenant-a", "user-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")
    issued = await service.issue_bind_code("tenant-a", "user-a")
    bound = await service.handle(
        StubImChannelAdapter(),
        _message("im-1", f"/bind {issued.code}", "m-bind"),
        verified=None,
    )
    assert bound.kind == "bound"
    return store, runtime, service


def _message(channel_user_id: str, content: str, message_id: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conv-{channel_user_id}",
        message_id=message_id,
        content=content,
        agent_id="assistant",
    )


def _verified(channel_user_id: str) -> VerifiedChannelIdentity:
    return VerifiedChannelIdentity(
        channel_type="stub-im",
        external_user_id=channel_user_id,
        verification_method="test",
        verified_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_AUTH01_anonymous_bound_identity_never_executes() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(), _message("im-1", "查机密数据", "m-forge"), verified=None
        )
        assert result.kind == "unbound"
        assert "/bind" in result.output
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_AUTH02_mismatched_verified_identity_rejected() -> None:
    from fluxion.services.channel_app import ChannelApplicationService as Svc

    store, _, service = await _setup()
    try:
        del service
        with pytest.raises(ChannelAuthError):
            await Svc(store, RecordingRuntime()).handle(
                StubImChannelAdapter(),
                _message("im-1", "查机密数据", "m-mismatch"),
                verified=_verified("im-2"),
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_AUTH02_matched_verified_identity_executes() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(),
            _message("im-1", "hello", "m-ok"),
            verified=_verified("im-1"),
        )
        assert result.kind == "message"
        assert result.platform_user_id == "user-a"
        assert len(runtime.requests) == 1
    finally:
        await store.close()
