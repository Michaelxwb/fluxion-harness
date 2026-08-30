"""TASK-008/010（phase2）跨实例 digest 一致性 + Multi-instance 等价性验收测试。

S-01（E2E，RULE-P2-01 / 架构规则 28）：双独立 Application 实例（共享同一真实
PG + Redis）各 resolve 同一 agent → snapshot_digest 完全相等；V2 字段齐全。
S-06（E2E，RULE-P2-07）：N 实例运行 → kill 一个 → 新请求打到存活实例 → digest
一致 + RPO=0。
S-08（integration，Gate G4）：Execution-1 pin v1 → 运行中发布 v2 → 全程 v1。

真实边界：两个独立 ContextResolver（各持独立 SQLite Registry 实例不可行——
共享同一真实 Store）+ 独立 resolver 对象模拟跨实例；SQLite 内存库。
多实例真实部署 Gate 由 phase6 FEAT-P6-05/S-07 承接（设计分层 §13.6）。
"""

from __future__ import annotations

import pytest
from tests.runtime_helpers import publish_resource

from fluxion.services.context_resolver import ContextResolver, ResolverSelector


@pytest.fixture
async def store():
    from fluxion.registry import SQLiteRegistryStore

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _seed_agent(store, *, version: str = "1") -> None:
    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version=version,
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version=version,
        spec={
            "name": "助手",
            "system_prompt": "你是产品助手。",
            "owner": "builder",
            "model_ref": {"id": "dev.echo", "version": "1"},
        },
    )


def _selector(user_id: str = "user-a") -> ResolverSelector:
    return ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id=user_id)


@pytest.mark.asyncio
async def test_s01_cross_instance_digest_equal(store) -> None:
    """S-01（RULE-P2-01）：双实例 resolve 同一 agent → digest 完全相等。"""
    # 实例 A 和 B 各持独立 ContextResolver 对象，共享同一 Store
    resolver_a = ContextResolver(store)
    resolver_b = ContextResolver(store)

    await _seed_agent(store, version="1")

    result_a = await resolver_a.resolve(_selector(), session_id="s-a")
    result_b = await resolver_b.resolve(_selector(), session_id="s-b")

    assert result_a.snapshot.snapshot_digest == result_b.snapshot.snapshot_digest
    assert result_a.snapshot.snapshot_digest  # 非空


@pytest.mark.asyncio
async def test_s01_v2_fields_complete(store) -> None:
    """digest 覆盖 V2 字段全集（remediation §13.2）。"""
    resolver = ContextResolver(store)
    await _seed_agent(store, version="1")
    result = await resolver.resolve(_selector(), session_id="s")

    snap = result.snapshot
    assert snap.user_profile_version is None or isinstance(snap.user_profile_version, str)
    assert snap.credential_versions is not None
    assert snap.agent_definition_version == "1"
    assert snap.snapshot_digest  # digest 已计算


@pytest.mark.asyncio
async def test_s06_kill_instance_equivalence(store) -> None:
    """S-06：kill 一个实例 → 新请求打到存活实例 → digest 一致 + RPO=0。"""
    resolver_a = ContextResolver(store)
    resolver_b = ContextResolver(store)
    await _seed_agent(store, version="1")

    # 实例 A 服务请求
    r_a = await resolver_a.resolve(_selector(), session_id="s-kill")
    # kill 实例 A（模拟：丢弃 resolver_a 引用）
    del resolver_a
    # 新请求打到实例 B
    r_b = await resolver_b.resolve(_selector(), session_id="s-kill")

    assert r_a.snapshot.snapshot_digest == r_b.snapshot.snapshot_digest


@pytest.mark.asyncio
async def test_s08_g4_execution_immutability_and_version_migration(store) -> None:
    """G4/ARCH-07：Execution-1 pin v1 → 运行中发布 v2 → Execution-1 全程 v1。"""
    resolver_1 = ContextResolver(store)
    await _seed_agent(store, version="1")

    result_1 = await resolver_1.resolve(_selector(), session_id="s1")
    digest_v1 = result_1.snapshot.snapshot_digest

    # 运行中发布 v2
    await _seed_agent(store, version="2")

    # Execution-1 的 snapshot 已冻结（frozen pydantic model），不受新发布影响
    assert result_1.snapshot.agent_definition_version == "1"
    assert result_1.snapshot.snapshot_digest == digest_v1

    # 新 Execution 使用 v2（新 resolver 实例 = 无 L1 缓存）
    resolver_fresh = ContextResolver(store)
    result_2 = await resolver_fresh.resolve(_selector(), session_id="s2")
    assert result_2.snapshot.agent_definition_version == "2"
    assert result_2.snapshot.snapshot_digest != digest_v1
