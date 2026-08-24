from __future__ import annotations

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)


@pytest.mark.asyncio
async def test_S_R02_new_runtime_profile_version_takes_effect_without_restart() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                prompt="保持严谨",
                model_policy={"provider": "dev.echo", "model": "v1", "timeout_ms": 1000},
            )
        )
        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )
        first = await service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="ping",
            )
        )

        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="2",
                prompt="保持严谨",
                model_policy={"provider": "dev.echo", "model": "v2", "timeout_ms": 1000},
            )
        )
        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="2",
            )
        )
        second = await service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="ping",
            )
        )

        assert first.service_instance_id == second.service_instance_id
        assert first.runtime_profile_version == "1"
        assert first.output == "v1: ping"
        assert second.runtime_profile_version == "2"
        assert second.output == "v2: ping"
        assert service.config_events[-1].kind is ResourceKind.RUNTIME_PROFILE
        assert service.config_events[-1].resource_id == "assistant"
        assert service.config_events[-1].version == "2"
        assert "spec_json" not in service.config_events[-1].to_payload()
    finally:
        await service.close()
