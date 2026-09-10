"""S-02 / FEAT-02: Service 发布时冻结绑定 Agent 配置。

D04 让 Agent 编辑 direct-effect，因此已发布的 Service 必须自带一份发布当时
所依据的 Agent 配置快照，否则线上运行会随 Agent 热改而漂移。

真实边界：真实 PostgreSQL + agent_service_binding + agent_definition 真实行。
连不上本地 PG 则 skip；所有创建行在用例结束时硬删除。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.models import (
    AgentDefinitionModel,
    AgentServiceBindingModel,
    ServiceDefinitionModel,
    ServiceReleaseModel,
)
from adapters.postgres.service_repository import ServiceRepository
from adapters.postgres.session import create_engine_and_session_factory

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)
DRAFT = {"name": "weekly-report", "goal": "send weekly summary"}


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


async def _teardown(
    factory: async_sessionmaker[AsyncSession],
    *,
    service_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    binding_id: uuid.UUID | None,
) -> None:
    async with factory() as session:
        async with session.begin():
            # circular FK: service_definition.current_release_id <-> service_release.service_id
            await session.execute(
                update(ServiceDefinitionModel)
                .where(ServiceDefinitionModel.id == service_id)
                .values(current_release_id=None)
            )
            await session.execute(
                delete(ServiceReleaseModel).where(ServiceReleaseModel.service_id == service_id)
            )
            if binding_id is not None:
                await session.execute(
                    delete(AgentServiceBindingModel).where(AgentServiceBindingModel.id == binding_id)
                )
            await session.execute(
                delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
            )
            if agent_id is not None:
                await session.execute(delete(AgentDefinitionModel).where(AgentDefinitionModel.id == agent_id))


async def test_publish_freezes_bound_agent_and_survives_later_agent_edit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """S-02: 发布后修改 Agent，已发布 release 的 Agent 快照不变。"""
    repo = ServiceRepository(factory)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session:
        async with session.begin():
            agent = AgentDefinitionModel(name=f"agent-{suffix}", instructions="v1", revision=1)
            service = ServiceDefinitionModel(
                service_key=f"svc-{suffix}", name="svc", goal="g", draft_payload=dict(DRAFT)
            )
            session.add_all([agent, service])
            await session.flush()
            binding = AgentServiceBindingModel(agent_id=agent.id, service_id=service.id)
            session.add(binding)
            await session.flush()
            agent_id, service_id, binding_id = agent.id, service.id, binding.id
    try:
        release = await repo.publish(service_id)
        frozen = release.published_payload["agent_snapshot"]
        assert frozen[str(agent_id)]["instructions"] == "v1"
        assert frozen[str(agent_id)]["revision"] == 1

        # D04 direct-effect: editing the Agent must not disturb the release
        async with factory() as session:
            async with session.begin():
                row = await session.get(AgentDefinitionModel, agent_id)
                assert row is not None
                row.instructions = "v2"
                row.revision = 2

        async with factory() as session:
            stored = await session.scalar(
                select(ServiceReleaseModel).where(ServiceReleaseModel.id == release.id)
            )
            assert stored is not None
            assert stored.published_payload["agent_snapshot"] == frozen
            assert stored.published_payload["agent_snapshot"][str(agent_id)]["instructions"] == "v1"
    finally:
        await _teardown(factory, service_id=service_id, agent_id=agent_id, binding_id=binding_id)


async def test_publish_without_bound_agent_is_still_valid(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """无绑定 Agent 时发布仍合法，快照为空。"""
    repo = ServiceRepository(factory)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session:
        async with session.begin():
            service = ServiceDefinitionModel(
                service_key=f"svc-{suffix}", name="svc", goal="g", draft_payload=dict(DRAFT)
            )
            session.add(service)
            await session.flush()
            service_id = service.id
    try:
        release = await repo.publish(service_id)
        assert release.published_payload["agent_snapshot"] == {}
    finally:
        await _teardown(factory, service_id=service_id, agent_id=None, binding_id=None)
