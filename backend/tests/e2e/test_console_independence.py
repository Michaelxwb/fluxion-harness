from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from tests.console_helpers import console_stack, create_resource, publish_resource

from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import publish_records
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RunRuntimeRequest, RuntimeApplicationService


async def test_S_C103_runtime_reads_registry_after_console_shutdown(tmp_path: Path) -> None:
    database = tmp_path / "console-independent.db"
    async with console_stack(db_path=database) as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C103",
        )
        assert published.json()["data"]["event_status"] == "pending"

    runtime_store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{database}")
    runtime = RuntimeApplicationService.create_dev_bundle(runtime_store)
    await runtime.initialize()
    try:
        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-S-C103",
                input_message="console is down",
            )
        )
        async with runtime_store.engine.connect() as connection:
            records = (await connection.execute(select(publish_records))).mappings().all()
        assert result.runtime_profile_version == "1"
        assert result.output == "console: console is down"
        assert len(records) == 1
        assert records[0]["request_id"] == "req-S-C103"
    finally:
        await runtime.close()
