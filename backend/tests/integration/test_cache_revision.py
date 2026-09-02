from __future__ import annotations

import asyncio

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import seed_agent_definition


@pytest.mark.asyncio
async def test_B_R01_revision_polling_recovers_after_lost_change_event() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime_service = RuntimeApplicationService.create_dev_bundle(
        store,
        cache_ttl_seconds=600,
    )
    publisher_service = RuntimeApplicationService.create_dev_bundle(
        store,
        cache_ttl_seconds=600,
    )
    await runtime_service.initialize()
    try:
        await runtime_service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                request_timeout_ms=1_000,
            )
        )
        # TASK-A104：persona/model 在 AgentDefinition；此处关注 revision 热生效。
        await seed_agent_definition(store, provider_id="dev.echo")
        await runtime_service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )
        first = await runtime_service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="cache",
            )
        )

        await publisher_service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="2",
                request_timeout_ms=1_000,
            )
        )
        await publisher_service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="2",
                notify_runtime=False,
            )
        )
        await asyncio.sleep(0.3)
        second = await runtime_service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="cache",
            )
        )

        assert first.runtime_profile_version == "1"
        assert runtime_service.config_events[-1].version == "1"
        assert second.runtime_profile_version == "2"
        # 模型名热切随 AgentDefinition/MODEL 链（TASK-004/008）；此处校验新版本
        # 配置已生效并回显本次输入（DevEcho 语义）。
        assert "cache" in second.output
        # revision = profile v1 + v2 两次 commit_publication + tenant-default
        # policy 的 put_binding（RULE-02 三维齐备 seeding，TASK-003 返工——
        # binding 变更同样 bump revision，属热生效语义的一部分）。
        assert await store.read_revision(tenant_id="tenant-a") == 3
        assert runtime_service.last_seen_revision("tenant-a") == 3
    finally:
        await runtime_service.close()
