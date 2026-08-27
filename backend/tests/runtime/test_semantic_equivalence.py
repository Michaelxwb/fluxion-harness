"""TASK-009 Runtime Semantic Equivalence 契约测试（BE-S-03 / BE-B-01）。

RULE-fluxion-runtime-001：相同 tenant+user+agent 在不同 Pod 解析出等价
RuntimeProfile/AgentDefinition，生成一致 ExecutionSnapshot；Snapshot frozen
语义保证已启动执行不受后续发布漂移影响（PRD §4.3 pinned versions）。

真实边界：两个 SQLiteRegistryStore 独立实例指向同一文件库（模拟双 Pod 读
同一 Registry），各自 L1 cache；seed 经真实 store.publish；无 mock。
"""

from __future__ import annotations

import pytest
from tests.runtime_helpers import publish_resource, seed_agent_definition, seed_skill

from fluxion.agents.definitions import AgentDefinition
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.agent import AgentRuntime
from fluxion.runtime.context import RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


def _stack(store: SQLiteRegistryStore) -> tuple[AgentRuntime, ResourceResolver]:
    resolver = ResourceResolver(store)
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(resolver),
        memory_store=InMemorySessionMemoryStore(),
        model_providers=None,  # 本任务只验证解析层等价，不触模型
    )
    return runtime, resolver


def _request(tenant: str = "tenant-a", user: str = "user-a", session: str = "s") -> RequestContext:
    return RequestContext(
        tenant_id=tenant,
        user_id=user,
        runtime_profile_id="assistant",
        session_id=session,
    )


async def _seed_bundle(store: SQLiteRegistryStore) -> None:
    """发布完整引用链：mechanics profile + agent(skill cap) + skill 资源。"""
    from fluxion.resources import ResourceDefinition, ResourceStatus

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec=AgentDefinition(
            name="assistant",
            description="equivalence fixture",
            system_prompt="保持严谨。",
            owner="fixture",
            model_ref={"id": "test", "version": "1"},
            capabilities=[
                {"capability_ref": "search", "version_pin": "2", "type": "skill"}
            ],
        ).model_dump(mode="json"),
    )
    await seed_skill(store, version="2")
    _ = (ResourceDefinition, ResourceStatus)


@pytest.mark.asyncio
async def test_be_s_03_two_pods_resolve_identical_snapshots(tmp_path) -> None:
    """BE-S-03：同一库、独立两 Pod 的 Resolver 逐字段产出一致快照。"""
    db_file = tmp_path / "registry.db"
    pod_a_store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{db_file}")
    pod_b_store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{db_file}")
    await pod_a_store.initialize()
    await pod_b_store.initialize()
    try:
        await _seed_bundle(pod_a_store)

        runtime_a, _ = _stack(pod_a_store)
        runtime_b, _ = _stack(pod_b_store)
        ctx_a = await runtime_a.start_execution(_request(session="pod-a"))
        ctx_b = await runtime_b.start_execution(_request(session="pod-b"))

        snap_a, snap_b = ctx_a.snapshot, ctx_b.snapshot
        # 稳定字段逐项等价（易变字段 execution/request/trace_id 不在此列）。
        stable_fields = (
            "tenant_id", "user_id", "runtime_profile_id", "runtime_profile_version",
            "agent_definition_id", "agent_definition_version",
            "system_prompt", "model_resolution",
            "skill_instructions", "skill_allowed_tools", "skill_versions",
            "mcp_versions", "plugin_versions", "binding_versions",
        )
        for field in stable_fields:
            assert getattr(snap_a, field) == getattr(snap_b, field), f"漂移字段: {field}"

        # PRD §4.3 pin 核对（Phase 1 已覆盖子集）。
        assert snap_a.runtime_profile_version == "1"
        assert snap_a.agent_definition_id == "assistant"
        assert snap_a.model_resolution.provider == "test"
        assert snap_a.skill_versions == {"search": "2"}
        assert snap_a.system_prompt == "保持严谨。"

        # Pod B 冷读：不依赖 Pod A 的任何进程内状态。
        assert snap_b.system_prompt == snap_a.system_prompt
    finally:
        await pod_a_store.close()
        await pod_b_store.close()


@pytest.mark.asyncio
async def test_be_b_01_pinned_agent_survives_hot_publish_of_v2(tmp_path) -> None:
    """BE-B-01：v1 pinned 执行中发布 v2——在途执行按 v1 收束；新执行取 v2。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _seed_bundle(store)
        runtime, _ = _stack(store)

        ctx = await runtime.start_execution(_request(session="live"))
        assert ctx.snapshot.agent_definition_version == "1"
        assert ctx.snapshot.skill_versions == {"search": "2"}

        # 热发布 v2（system_prompt 变更可见区分）。
        from tests.runtime_helpers import publish_resource

        await publish_resource(
            store,
            tenant_id="tenant-a",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="2",
            spec={"request_timeout_ms": 5_000, "max_retries": 1},
        )
        await publish_resource(
            store,
            tenant_id="tenant-a",
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="2",
            spec=AgentDefinition(
                name="assistant",
                description="equivalence fixture",
                system_prompt="v2 严谨提示。",
                owner="fixture",
                model_ref={"id": "test", "version": "1"},
                capabilities=[
                    {"capability_ref": "search", "version_pin": "2", "type": "skill"}
                ],
            ).model_dump(mode="json"),
        )

        # 在途执行继续用 v1 快照——pydantic frozen 保证不可换绑。
        result = await runtime.run_step(ctx, "继续")
        assert result.snapshot.agent_definition_version == "1"
        assert result.snapshot.system_prompt == "保持严谨。"

        # 新执行解析到 v2。
        fresh = await runtime.start_execution(_request(session="fresh"))
        assert fresh.snapshot.agent_definition_version == "2"
        assert fresh.snapshot.system_prompt == "v2 严谨提示。"
    finally:
        await store.close()
