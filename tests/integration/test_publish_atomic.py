"""S-02: Service 发布原子切换（integration，真实 PG）。

validate + snapshot persist + current pointer switch 必须在同一事务内；
失败不得留下半发布状态。需要本地 PG（见 .env）；连不上则 skip。
"""

import os
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.models import ServiceDefinitionModel, ServiceReleaseModel
from adapters.postgres.service_repository import ServiceRepository
from adapters.postgres.session import create_engine_and_session_factory
from framework.web.errors import AppError

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)
DRAFT = {"name": "weekly-report", "goal": "send weekly summary"}


@pytest.fixture()
async def factory() -> AsyncSession:
    engine: AsyncEngine
    try:
        engine, factory = create_engine_and_session_factory(TEST_DATABASE_URL)
        async with engine.connect():
            pass
    except Exception as exc:
        pytest.skip(f"no local PG for integration test: {exc}")
    yield factory  # type: ignore[misc]
    await engine.dispose()


async def _make_service(factory: async_sessionmaker[AsyncSession], key: str, draft: dict | None) -> uuid.UUID:
    async with factory() as session:
        async with session.begin():
            row = ServiceDefinitionModel(service_key=key, name="svc", goal="g", draft_payload=draft)
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


async def test_publish_switches_current_pointer_atomically(factory: async_sessionmaker[AsyncSession]) -> None:
    repo = ServiceRepository(factory)
    service_id = await _make_service(factory, f"s02-{uuid.uuid4().hex[:8]}", dict(DRAFT))
    try:
        release = await repo.publish(service_id)
        assert release.content_hash
        async with factory() as session:
            service = await session.scalar(
                select(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
            )
            assert service is not None
            assert service.current_release_id == release.id
            assert service.status == "published"
            # republish identical content: idempotent, no duplicate row
            again = await repo.publish(service_id)
            assert again.id == release.id
            count = await session.scalar(
                select(func.count())
                .select_from(ServiceReleaseModel)
                .where(ServiceReleaseModel.service_id == service_id)
            )
            assert count == 1
    finally:
        await _teardown(factory, service_id)


async def test_failed_publish_leaves_no_half_state(factory: async_sessionmaker[AsyncSession]) -> None:
    repo = ServiceRepository(factory)
    service_id = await _make_service(factory, f"s02-bad-{uuid.uuid4().hex[:8]}", {"name": "no-goal"})
    try:
        with pytest.raises(AppError):
            await repo.publish(service_id)
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
