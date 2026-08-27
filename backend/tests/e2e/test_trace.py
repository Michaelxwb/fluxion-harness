from __future__ import annotations

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    HookScope,
    TypedEventBus,
)
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
    ToolCallRequest,
)


@pytest.mark.asyncio
async def test_S_R09_trace_contains_snapshot_model_tool_hook_latency_and_error() -> None:
    observed_tools: list[str] = []
    event_bus = TypedEventBus()

    async def record_tool(payload: BeforeToolCallPayload) -> None:
        observed_tools.append(payload.tool_id)

    event_bus.register(
        HookRegistration(
            registration_id="trace-hook",
            event_type=BeforeToolCallPayload,
            priority=10,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=record_tool,
        )
    )
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService.create_dev_bundle(
        store,
        cache_ttl_seconds=600,
        event_bus=event_bus,
    )
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                request_timeout_ms=1_000,
            )
        )
        from tests.runtime_helpers import seed_agent_definition
        # 工具准入（time.now）随 AgentDefinition TOOL capability。
        await seed_agent_definition(
            store,
            provider_id="dev.echo",
            capabilities=[{"capability_ref": "time.now", "version_pin": "1", "type": "tool"}],
        )

        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )

        result = await service.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="trace",
                tool_calls=[ToolCallRequest(tool_id="time.now", arguments={})],
            )
        )
        trace = await service.trace_store.get(result.trace_id)

        assert observed_tools == ["time.now"]
        assert trace is not None
        assert trace.snapshot.runtime_profile_version == "1"
        assert trace.model == {"provider_id": "dev.echo", "tool_call_count": 0}
        assert trace.tools[0]["tool_id"] == "time.now"
        assert trace.hooks[0]["registration_id"] == "trace-hook"
        assert trace.latency_ms >= 0
        assert trace.error is None
        event_names = [event.name for event in trace.events]
        assert "model.completed" in event_names
        assert "tool.completed" in event_names
        assert "hook.completed" in event_names
    finally:
        await service.close()
