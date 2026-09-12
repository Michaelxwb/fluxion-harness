"""S-05 / E-04: resource_scope_type 由 Registry 校验并冻结 schema_hash（RULE-05）。

真实边界：真实 PostgreSQL + 真实 service_release 行 + 真实 ResourceScopeRegistry，
不 mock 校验核心。连不上本地 PG 则 skip；所有创建行在用例结束时硬删除。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.models import ServiceDefinitionModel, ServiceReleaseModel
from adapters.postgres.service_repository import ServiceRepository
from adapters.postgres.session import create_engine_and_session_factory
from framework.contracts.resource_scope import SERVICE_SCOPE_TYPE_UNKNOWN
from framework.integration.resource_scope_registry import ResourceScopeRegistry
from framework.web.errors import AppError

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)
SCHEMA = {"type": "object", "required": ["tenant_id"], "properties": {"tenant_id": {"type": "string"}}}


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    try:
        engine: AsyncEngine
        engine, session_factory = create_engine_and_session_factory(TEST_DATABASE_URL)
        async with engine.connect():
            pass
    except Exception as exc:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but the database is unreachable: {exc}")
        pytest.skip(f"no local PG for integration test: {exc}")
    yield session_factory
    await engine.dispose()


@pytest.fixture()
def registry() -> ResourceScopeRegistry:
    return ResourceScopeRegistry({"tenant": {"schema": SCHEMA}})


async def _make_service(
    factory: async_sessionmaker[AsyncSession],
    key: str,
    draft: dict[str, object],
    test_actor_id: uuid.UUID,
) -> uuid.UUID:
    async with factory() as session:
        async with session.begin():
            row = ServiceDefinitionModel(
                key=key, name="svc", description="g", draft_payload=draft, created_by=test_actor_id
            )
            session.add(row)
            await session.flush()
            return row.id


async def _teardown(factory: async_sessionmaker[AsyncSession], service_id: uuid.UUID) -> None:
    # circular FK: service_definition.current_release_id <-> service_release.service_id
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(ServiceDefinitionModel)
                .where(ServiceDefinitionModel.id == service_id)
                .values(current_release_id=None)
            )
            await session.execute(
                delete(ServiceReleaseModel).where(ServiceReleaseModel.service_id == service_id)
            )
            await session.execute(
                delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
            )


async def test_s05_declared_scope_freezes_registry_schema_hash(
    factory: async_sessionmaker[AsyncSession],
    registry: ResourceScopeRegistry,
    test_actor_id: uuid.UUID,
) -> None:
    """S-05: 合法 scope 发布成功，payload 含 registry 派生的 schema_hash。"""
    declared = registry.get("tenant")
    assert declared is not None
    repo = ServiceRepository(factory, scope_registry=registry)
    draft: dict[str, object] = {"name": "n", "goal": "g", "resource_scope_type": "tenant"}
    service_id = await _make_service(factory, f"s05-{uuid.uuid4().hex[:8]}", draft, test_actor_id)
    try:
        release = await repo.publish(service_id, request_id="req-s05")
        payload = release.published_payload
        assert payload["resource_scope_type"] == "tenant"
        assert payload["resource_scope_schema_hash"] == declared.schema_hash
    finally:
        await _teardown(factory, service_id)


async def test_e04_unknown_scope_type_rejected_without_writing_release(
    factory: async_sessionmaker[AsyncSession],
    registry: ResourceScopeRegistry,
    test_actor_id: uuid.UUID,
) -> None:
    """E-04: 发布侧未知 type 抛 SERVICE_SCOPE_TYPE_UNKNOWN（422），且无 release 行落库。

    发布侧是**管理员输入错**（作者可修），与执行侧的
    SERVICE_CONFIGURATION_INVALID(500)（框架冻结态与 registry 不一致）分开。
    """
    repo = ServiceRepository(factory, scope_registry=registry)
    draft: dict[str, object] = {"name": "n", "goal": "g", "resource_scope_type": "customer"}
    service_id = await _make_service(factory, f"e04-{uuid.uuid4().hex[:8]}", draft, test_actor_id)
    try:
        with pytest.raises(AppError) as exc_info:
            await repo.publish(service_id)
        assert exc_info.value.code == SERVICE_SCOPE_TYPE_UNKNOWN
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ServiceReleaseModel)
                .where(ServiceReleaseModel.service_id == service_id)
            )
            assert count == 0
            service = await session.scalar(
                select(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
            )
            assert service is not None
            assert service.current_release_id is None
    finally:
        await _teardown(factory, service_id)


async def test_service_without_declared_scope_still_publishes(
    factory: async_sessionmaker[AsyncSession],
    registry: ResourceScopeRegistry,
    test_actor_id: uuid.UUID,
) -> None:
    """未声明 scope 的 Service 保持可发布，payload 两个 scope 字段均为 None。"""
    repo = ServiceRepository(factory, scope_registry=registry)
    service_id = await _make_service(
        factory, f"noscope-{uuid.uuid4().hex[:8]}", {"name": "n", "goal": "g"}, test_actor_id
    )
    try:
        release = await repo.publish(service_id)
        assert release.published_payload["resource_scope_type"] is None
        assert release.published_payload["resource_scope_schema_hash"] is None
    finally:
        await _teardown(factory, service_id)
