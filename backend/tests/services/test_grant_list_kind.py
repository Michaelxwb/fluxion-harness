"""TASK-008（FEAT-07）list_grants 补 kind 验收测试（S-06）。"""

from __future__ import annotations

from tests.runtime_helpers import TEST_POSTGRES_DSN

from datetime import UTC, datetime

from fluxion.agents.definitions import AgentCapabilityReference, CapabilityType
from fluxion.registry import PlatformUserRecord, PostgreSQLRegistryStore
from fluxion.users.service import UserDomainService


async def _seed_user(store: PostgreSQLRegistryStore) -> None:
    await store.create_platform_user(
        PlatformUserRecord(
            tenant_id="t1",
            platform_user_id="u1",
            display_name="U1",
            created_at=datetime.now(UTC),
        )
    )


async def test_S06_list_grants_returns_kind() -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        svc = UserDomainService(store)
        await _seed_user(store)
        granted = await svc.grant(
            tenant_id="t1",
            platform_user_id="u1",
            capability_binding=AgentCapabilityReference(
                capability_ref="tool_1", version_pin="1", type=CapabilityType.TOOL
            ),
        )
        assert granted["resource_kind"] == "tool"
        grants = await svc.list_grants(tenant_id="t1", platform_user_id="u1")
        assert len(grants) == 1
        assert grants[0]["resource_kind"] == "tool"
        assert grants[0]["capability_ref"] == "tool_1"
    finally:
        await store.close()
