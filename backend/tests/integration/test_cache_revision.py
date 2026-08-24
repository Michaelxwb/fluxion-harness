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
                prompt="保持严谨",
                model_policy={"provider": "dev.echo", "model": "v1", "timeout_ms": 1000},
            )
        )
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
                prompt="保持严谨",
                model_policy={"provider": "dev.echo", "model": "v2", "timeout_ms": 1000},
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
        assert second.output == "v2: cache"
        assert await store.read_revision(tenant_id="tenant-a") == 2
        assert runtime_service.last_seen_revision("tenant-a") == 2
    finally:
        await runtime_service.close()
