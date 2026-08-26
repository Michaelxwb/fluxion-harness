from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.console_helpers import create_resource, publish_resource, runtime_profile_spec

from fluxion.api.console import create_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import outbox_events
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.outbox import InProcessConfigEventPublisher, OutboxWorker
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)


async def test_S_C102_publish_event_invalidates_runtime_for_new_execution() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    console = ConsoleApplicationService(store)
    await runtime.initialize()
    client = AsyncClient(transport=ASGITransport(app=create_app(console)), base_url="http://console")
    try:
        await runtime.create_runtime_profile(_runtime_profile("1", "v1"))
        await runtime.publish_runtime_profile(
            PublishRuntimeProfileRequest("tenant-a", "assistant", "1")
        )
        first = await runtime.run(_run_request("before"))

        await create_resource(
            client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="2",
            spec=_console_spec("2", "v2"),
        )
        published = await publish_resource(
            client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="2",
            expected_base_version="1",
            request_id="req-S-C102",
        )
        worker = OutboxWorker(
            store,
            InProcessConfigEventPublisher(runtime.handle_config_changed),
            worker_id="worker-S-C102",
        )
        dispatched = await worker.run_once()
        second = await runtime.run(_run_request("after"))

        async with store.engine.connect() as connection:
            outbox = (await connection.execute(select(outbox_events))).mappings().all()
        assert published.json()["data"]["event_status"] == "pending"
        # A8：runtime bootstrap 发布（v1）现也走治理事务写 outbox，故 v1+v2 共 2 条
        # 事件，均被 worker 派发为 published。
        assert dispatched.published == 2
        assert len(outbox) == 2
        assert all(row["status"] == "published" for row in outbox)
        assert first.runtime_profile_version == "1"
        assert second.runtime_profile_version == "2"
        assert second.output == "v2: after"
        assert runtime.config_events[-1].version == "2"
    finally:
        await client.aclose()
        await runtime.close()


def _runtime_profile(version: str, model: str) -> CreateRuntimeProfileRequest:
    return CreateRuntimeProfileRequest(
        tenant_id="tenant-a",
        runtime_profile_id="assistant",
        version=version,
        prompt="保持严谨",
        model_policy={"provider": "dev.echo", "model": model, "timeout_ms": 1000},
    )


def _console_spec(version: str, model: str) -> dict[str, object]:
    return {
        **runtime_profile_spec(display_name=f"assistant-{version}"),
        "model_policy": {"provider": "dev.echo", "model": model, "timeout_ms": 1000},
    }


def _run_request(message: str) -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id=f"session-{message}",
        input_message=message,
    )
