"""TASK-003（phase2）pgvector SemanticStore provider 验收测试。

S-07（E2E，fluxion-resource-registry / fluxion-resource-registry 双库契约）：
- recall 语义：cosine 相关性排序 + memory_type 过滤 + tenant/user 隔离；
- 双库契约：SQLite 与 PostgreSQL 实现同一 provider 契约
  （PG 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控，复用 local-pg-test-env）；
- pgvector 扩展不可用时降级 JSON+Python cosine（记录于 Evidence，不伪造 GREEN）。

真实边界：真实 PG/SQLite personal_memory 表 + 真实 embedding 计算；不 mock。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore
from fluxion.registry import PostgreSQLRegistryStore, SQLiteRegistryStore


def _sqlite_factory() -> SQLiteRegistryStore:
    return SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")


def _postgres_factory() -> PostgreSQLRegistryStore:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
    )
    return PostgreSQLRegistryStore(dsn, reset_on_initialize=True)


def _store_params() -> list[Any]:
    params: list[Any] = [pytest.param(_sqlite_factory, id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param(_postgres_factory, id="postgres"))
    return params


@pytest.fixture(params=_store_params())
async def store(request: pytest.FixtureRequest) -> AsyncGenerator[Any, None]:
    instance = request.param()
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
async def semantic(store: Any) -> PgVectorSemanticStore:
    provider = PgVectorSemanticStore(store.engine)
    await provider.initialize()
    return provider


def _vec(base: float, delta: float) -> list[float]:
    return [base, base + delta, base - delta]


@pytest.mark.asyncio
async def test_s07_recall_ranks_by_cosine_and_filters_memory_type(
    semantic: PgVectorSemanticStore,
) -> None:
    user = "user-s7"
    # semantic 类：两条（一条与 query 同向、一条偏离）；episodic 类：一条（应被过滤）
    await semantic.store(
        "tenant-a",
        user,
        {
            "memory_type": "semantic",
            "content": "报告先给结论",
            "embedding": _vec(1.0, 0.0),
            "source_session_id": "s1",
        },
    )
    await semantic.store(
        "tenant-a",
        user,
        {
            "memory_type": "semantic",
            "content": "偏好深色主题",
            "embedding": _vec(0.0, 1.0),
            "source_session_id": "s1",
        },
    )
    await semantic.store(
        "tenant-a",
        user,
        {
            "memory_type": "episodic",
            "content": "昨天聊过部署",
            "embedding": _vec(1.0, 0.0),
            "source_session_id": "s1",
        },
    )

    hits = await semantic.recall(
        "tenant-a",
        user,
        query="报告格式偏好",
        query_embedding=_vec(1.0, 0.05),
        memory_type="semantic",
    )
    assert len(hits) >= 1
    # 同向（cosine≈1）排在偏离（cosine≈0）之前
    assert hits[0]["content"] == "报告先给结论"
    # memory_type 过滤：episodic 不出现
    assert all(h["memory_type"] == "semantic" for h in hits)


@pytest.mark.asyncio
async def test_s07_tenant_user_isolation(semantic: PgVectorSemanticStore) -> None:
    await semantic.store(
        "tenant-a",
        "user-iso-a",
        {
            "memory_type": "semantic",
            "content": "tenant-a secret",
            "embedding": _vec(1.0, 0.0),
            "source_session_id": "s",
        },
    )
    hits_other_user = await semantic.recall(
        "tenant-a", "user-iso-b", query="secret", query_embedding=_vec(1.0, 0.0)
    )
    hits_other_tenant = await semantic.recall(
        "tenant-b", "user-iso-a", query="secret", query_embedding=_vec(1.0, 0.0)
    )
    assert hits_other_user == []
    assert hits_other_tenant == []
