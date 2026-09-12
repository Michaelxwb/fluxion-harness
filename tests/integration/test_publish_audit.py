"""T6: 发布 audit event（integration，真实 PG）。

§3.5: 记录 entity_id/release_id/content_hash + request_id，
不记录完整敏感 payload。连不上本地 PG 则 skip。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.models import AuditLogModel, ServiceDefinitionModel, ServiceReleaseModel
from adapters.postgres.service_repository import ServiceRepository
from adapters.postgres.session import create_engine_and_session_factory

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)


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


async def test_publish_emits_audit_event_without_sensitive_payload(
    factory: async_sessionmaker[AsyncSession],
    test_actor_id: uuid.UUID,
) -> None:
    repo = ServiceRepository(factory)
    key = f"audit-{uuid.uuid4().hex[:8]}"
    async with factory() as session:
        async with session.begin():
            service = ServiceDefinitionModel(
                key=key,
                name="n",
                description="g",
                draft_payload={"name": "n", "goal": "g"},
                created_by=test_actor_id,
            )
            session.add(service)
            await session.flush()
            service_id = service.id
    try:
        release = await repo.publish(service_id, request_id="req-audit-1")
        async with factory() as session:
            events = (
                await session.scalars(
                    select(AuditLogModel).where(
                        AuditLogModel.resource_type == "service_release",
                        AuditLogModel.resource_id == str(release.id),
                    )
                )
            ).all()
            assert len(events) == 1
            event = events[0]
            assert event.action == "service.published"
            assert event.request_id == "req-audit-1"
            assert event.after_digest["release_no"] == release.release_no
            assert event.details["content_hash"] == release.content_hash
            assert event.details["release_no"] == release.release_no
            assert "draft" not in event.details
            assert "published_payload" not in event.details
            # idempotent republish emits no duplicate audit event
            await repo.publish(service_id, request_id="req-audit-2")
            count = len(
                (
                    await session.scalars(
                        select(AuditLogModel).where(
                            AuditLogModel.resource_type == "service_release",
                            AuditLogModel.resource_id == str(release.id),
                        )
                    )
                ).all()
            )
            assert count == 1
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(AuditLogModel).where(
                        AuditLogModel.resource_type == "service_release",
                        AuditLogModel.resource_id == str(release.id),
                    )
                )
                svc = await session.scalar(
                    select(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
                )
                if svc is not None:
                    svc.current_release_id = None
                await session.execute(
                    delete(ServiceReleaseModel).where(ServiceReleaseModel.service_id == service_id)
                )
                await session.execute(
                    delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
                )
