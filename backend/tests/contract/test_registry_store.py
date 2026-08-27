"""S-R07 / S-R10 RegistryStore 契约测试。

- S-R07（integration）：SQLiteRegistryStore 与 PostgreSQLRegistryStore 对同一
  Fixture 返回相同语义。同一套契约断言参数化跑两种 Store —— SQLite 恒执行；
  PostgreSQL 由环境变量 FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控（S-R10，
  需要真实 PostgreSQL / testcontainers）。

- RULE-13：Runtime 只依赖 RegistryStore Contract，不依赖具体 Store。因此本
  文件是契约的唯一事实源：任何 Store 实现必须通过同一套断言。

- E-R04 明文 Credential 拒绝（unit，test_resource_schema.py）与 E-R07 跨
  tenant 隔离（integration，test_tenant_registry.py）分别在各自的测试文件中，
  本文件只覆盖 Store 的 CRUD / 版本选择 / Binding / 并发冲突契约语义。
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest

from fluxion.registry import (
    ChannelRegistryStore,
    ChatAccessRecord,
    NotFoundError,
    OutboxStatus,
    PlatformUserRecord,
    PostgreSQLRegistryStore,
    PublicationCommand,
    PublicationOperation,
    RegistryStore,
    SQLiteRegistryStore,
    VersionConflictError,
)
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)

# ---------------------------------------------------------------------------
# Store 工厂：契约套件参数化的来源
# ---------------------------------------------------------------------------


def _sqlite_factory() -> ChannelRegistryStore:
    return SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")


def _postgres_factory() -> ChannelRegistryStore:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/fluxion_test",
    )
    return PostgreSQLRegistryStore(dsn, reset_on_initialize=True)


def _store_params() -> list[Any]:
    params: list[Any] = [pytest.param(_sqlite_factory, id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param(_postgres_factory, id="postgres"))
    return params


@pytest.fixture(params=_store_params())
async def store(request: pytest.FixtureRequest) -> AsyncGenerator[ChannelRegistryStore, None]:
    """为契约套件提供一种 Store 实例（SQLite 恒有；Postgres 门控）。"""
    factory: Any = request.param
    store = factory()
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# 公共 Fixture 数据
# ---------------------------------------------------------------------------


def _definition(
    *,
    kind: ResourceKind,
    id: str,
    tenant_id: str,
    version: str,
    status: ResourceStatus = ResourceStatus.DRAFT,
    spec: dict[str, object] | None = None,
) -> ResourceDefinition:
    return ResourceDefinition(
        kind=kind,
        id=id,
        tenant_id=tenant_id,
        version=version,
        status=status,
        spec_json=spec or {"name": id, "prompt": "you are helpful"},
    )


# ---------------------------------------------------------------------------
# S-R07 契约：CRUD 语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_S_R07_crud_roundtrip(store: RegistryStore) -> None:
    """put 后 get 返回同一语义；不存在返回 None（不抛错）。"""
    defn = _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")

    await store.put(defn)

    got = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="1")
    assert got is not None
    assert got.id == "asst"
    assert got.tenant_id == "t1"
    assert got.version == "1"
    assert got.spec_json == defn.spec_json

    missing = await store.get(ResourceKind.RUNTIME_PROFILE, "nope", tenant_id="t1", version="1")
    assert missing is None


@pytest.mark.asyncio
async def test_S_R07_version_selection(store: RegistryStore) -> None:
    """同一 id 多版本：get(version=...) 精确命中；无发布版本时 latest-published 不可见。"""
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")
    )
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="2")
    )

    v1 = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="1")
    assert v1 is not None and v1.version == "1"
    v2 = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="2")
    assert v2 is not None and v2.version == "2"


@pytest.mark.asyncio
async def test_S_R07_list_versions_is_paginated_and_tenant_scoped(
    store: RegistryStore,
) -> None:
    for tenant_id, version in (("t1", "1"), ("t1", "2"), ("t2", "3")):
        await store.put(
            _definition(
                kind=ResourceKind.WORKFLOW,
                id="weekly-report",
                tenant_id=tenant_id,
                version=version,
            )
        )

    first_page, total = await store.list_versions(
        ResourceKind.WORKFLOW,
        "weekly-report",
        tenant_id="t1",
        offset=0,
        limit=1,
    )

    assert total == 2
    assert len(first_page) == 1
    assert first_page[0].tenant_id == "t1"


@pytest.mark.asyncio
async def test_S_R07_list_resources_uses_latest_published_version(
    store: RegistryStore,
) -> None:
    for tenant_id, resource_id, version in (
        ("t1", "alpha", "1"),
        ("t1", "alpha", "2"),
        ("t1", "beta", "1"),
        ("t2", "private", "1"),
    ):
        await store.put(
            _definition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id=resource_id,
                tenant_id=tenant_id,
                version=version,
            )
        )
        await store.publish(
            ResourceKind.RUNTIME_PROFILE,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )

    resources, total = await store.list_resources(
        ResourceKind.RUNTIME_PROFILE,
        tenant_id="t1",
        offset=0,
        limit=1,
    )

    assert total == 2
    assert [(resource.id, resource.version) for resource in resources] == [("alpha", "2")]


@pytest.mark.asyncio
async def test_S_R07_publish_and_latest(store: RegistryStore) -> None:
    """发布后 latest-published 命中；未发布版本对默认选择器不可见。"""
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")
    )
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="2")
    )

    published = await store.publish(
        ResourceKind.RUNTIME_PROFILE,
        "asst",
        tenant_id="t1",
        version="2",
    )
    assert published.version == "2"
    assert published.status == ResourceStatus.PUBLISHED

    latest = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1")
    assert latest is not None and latest.version == "2"

    # 未发布的 v1 对 latest-published 不可见，但精确版本仍可取
    assert latest.status == ResourceStatus.PUBLISHED
    v1 = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="1")
    assert v1 is not None and v1.status == ResourceStatus.DRAFT


@pytest.mark.asyncio
async def test_S_R07_no_published_no_latest(store: RegistryStore) -> None:
    """没有任何发布版本时，latest-published 返回 None。"""
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")
    )
    latest = await store.get(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1")
    assert latest is None


@pytest.mark.asyncio
async def test_S_R07_published_immutable(store: RegistryStore) -> None:
    """Published 版本不可原地修改：对已发布 id+version 再次 put 必须抛 VersionConflictError。"""
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")
    )
    await store.publish(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="1")

    with pytest.raises(VersionConflictError):
        await store.put(
            _definition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id="asst",
                tenant_id="t1",
                version="1",
                spec={"name": "mutated"},
            )
        )


@pytest.mark.asyncio
async def test_S_R07_publish_missing_version_not_found(store: RegistryStore) -> None:
    """发布不存在的版本抛 NotFoundError，不静默成功。"""
    with pytest.raises(NotFoundError):
        await store.publish(ResourceKind.RUNTIME_PROFILE, "asst", tenant_id="t1", version="9")


@pytest.mark.asyncio
async def test_publication_outbox_contract_is_shared_by_sqlite_and_postgres(
    store: RegistryStore,
) -> None:
    await store.put(
        _definition(kind=ResourceKind.RUNTIME_PROFILE, id="atomic", tenant_id="t1", version="1")
    )
    commit = await store.commit_publication(
        PublicationCommand(
            publish_id="pub-contract",
            event_id="evt-contract",
            tenant_id="t1",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="atomic",
            version="1",
            operation=PublicationOperation.PUBLISH,
            actor_id="contract-tester",
            request_id="req-contract",
            trace_id="trace-contract",
            expected_base_version="1",
        )
    )
    claimed = await store.claim_outbox(worker_id="contract-worker", limit=10, lease_seconds=5)

    assert commit.resource.status is ResourceStatus.PUBLISHED
    assert commit.event_status is OutboxStatus.PENDING
    assert commit.revision == 1
    assert len(claimed) == 1
    assert claimed[0].event_id == "evt-contract"
    assert claimed[0].revision == 1
    await store.mark_outbox_published("evt-contract", worker_id="contract-worker")
    assert await store.claim_outbox(worker_id="contract-worker", limit=10, lease_seconds=5) == []
    assert await store.read_revision(tenant_id="t1") == 1


@pytest.mark.asyncio
async def test_S_R07_binding_roundtrip(store: RegistryStore) -> None:
    """Binding 写入与按 subject 读取；disabled 不返回。"""
    binding = ResourceBinding(
        binding_id="b1",
        tenant_id="t1",
        subject_type="user",
        subject_id="u1",
        resource_type=ResourceKind.SKILL,
        resource_id="code-review",
        resource_version_selector="latest-published",
        config_json={"params": {}},
        credential_ref="secret://t1/skills/code-review",
        enabled=True,
    )

    await store.put_binding(binding)

    got = await store.list_bindings(subject_type="user", subject_id="u1", tenant_id="t1")
    assert len(got) == 1
    assert got[0].resource_id == "code-review"
    assert got[0].credential_ref == "secret://t1/skills/code-review"

    await store.disable_binding(binding.binding_id, tenant_id="t1")
    after = await store.list_bindings(subject_type="user", subject_id="u1", tenant_id="t1")
    assert after == []


@pytest.mark.asyncio
async def test_S_P13_04_platform_user_and_chat_access_contract(
    store: ChannelRegistryStore,
) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    user = PlatformUserRecord(
        tenant_id="t1",
        platform_user_id="u1",
        display_name="User 1",
        created_at=now,
    )
    await store.create_platform_user(user)

    loaded = await store.get_platform_user(tenant_id="t1", platform_user_id="u1")
    users, total = await store.list_platform_users(tenant_id="t1", offset=0, limit=10)
    other_users, other_total = await store.list_platform_users(
        tenant_id="t2", offset=0, limit=10
    )

    assert loaded == user
    assert users == [user]
    assert total == 1
    assert other_users == []
    assert other_total == 0

    access = ChatAccessRecord(
        access_id="access-1",
        tenant_id="t1",
        platform_user_id="u1",
        agent_id="assistant",
        token_hash="a" * 64,
        created_at=now,
    )
    await store.create_chat_access(access)
    assert await store.resolve_chat_access(token_hash="a" * 64) == access

    revoked = await store.revoke_chat_access(
        tenant_id="t1",
        access_id="access-1",
        revoked_at=now,
    )
    assert revoked.revoked_at == now
    assert await store.resolve_chat_access(token_hash="a" * 64) is None


# ---------------------------------------------------------------------------
# S-R07 契约：并发冲突语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_S_R07_concurrent_put_version_conflict(store: RegistryStore) -> None:
    """同一 id+version 并发 put：恰好一个成功，其余抛 VersionConflictError。"""
    defn = _definition(kind=ResourceKind.RUNTIME_PROFILE, id="asst", tenant_id="t1", version="1")

    import asyncio

    async def _put() -> str:
        await store.put(defn)
        return "ok"

    results = await asyncio.gather(
        _put(),
        _put(),
        _put(),
        return_exceptions=True,
    )

    ok_count = sum(1 for r in results if r == "ok")
    conflict_count = sum(1 for r in results if isinstance(r, VersionConflictError))
    assert ok_count == 1
    assert conflict_count == 2
