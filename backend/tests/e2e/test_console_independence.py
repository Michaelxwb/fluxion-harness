from __future__ import annotations

from sqlalchemy import select

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.schema import publish_records
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RunRuntimeRequest, RuntimeApplicationService
from tests.console_helpers import console_stack, create_resource, publish_resource
from tests.runtime_helpers import TEST_POSTGRES_DSN


async def test_S_C103_runtime_reads_registry_after_console_shutdown() -> None:
    async with console_stack() as stack:
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
        # TASK-A104：persona/model 在同名 AgentDefinition（console 侧同库种入）。
        from tests.runtime_helpers import seed_agent_definition
        await seed_agent_definition(stack.store, provider_id="dev.echo", model_name="dev")
        assert published.json()["data"]["event_status"] == "pending"

    # PG-Only：runtime 直连同一 PG 库（不重建，否则 wipe 掉 console 种的数据），验证 console 关闭后数据仍在。
    runtime_store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN)
    runtime = RuntimeApplicationService.create_dev_bundle(runtime_store)
    await runtime.initialize()
    try:
        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                agent_definition_id="assistant",
                runtime_profile_id="assistant",
                session_id="session-S-C103",
                input_message="console is down",
            )
        )
        async with runtime_store.engine.connect() as connection:
            records = (await connection.execute(select(publish_records))).mappings().all()
        assert result.runtime_profile_version == "1"
        assert result.output == "dev: console is down"  # 模型名归 MODEL 链
        assert len(records) == 1
        assert records[0]["request_id"] == "req-S-C103"
    finally:
        await runtime.close()
