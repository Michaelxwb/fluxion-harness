"""Shared fixtures for real-PostgreSQL integration tests.

`service_definition.created_by` is NOT NULL and FK-bound to `platform_user`
(ADR-052: the creator identity is the basis of the Builder execution-visible
scope). Tests must therefore seed an actor before creating a Service; this
module provides one shared, deterministic actor per test session.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select, update

from adapters.postgres.models import (
    PlatformUserModel,
    ServiceDefinitionModel,
    ServiceReleaseModel,
)
from adapters.postgres.session import create_engine_and_session_factory

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)

# Deterministic key so concurrent/repeated sessions converge on one actor row.
TEST_ACTOR_KEY = "it-test-actor"


@pytest.fixture(scope="session")
async def test_actor_id() -> AsyncIterator[uuid.UUID]:
    """Seed one ACTIVE platform_user and return its id (session scoped)."""
    try:
        engine, session_factory = create_engine_and_session_factory(TEST_DATABASE_URL)
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - depends on local PG availability
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but the database is unreachable: {exc}")
        pytest.skip(f"no local PG for integration test: {exc}")

    try:
        async with session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(PlatformUserModel).where(
                        PlatformUserModel.tenant_id == "default",
                        PlatformUserModel.user_key == TEST_ACTOR_KEY,
                        PlatformUserModel.is_deleted.is_(False),
                    )
                )
                if existing is not None:
                    actor_id = existing.id
                else:
                    row = PlatformUserModel(
                        user_key=TEST_ACTOR_KEY,
                        display_name="integration test actor",
                        role="BUILDER",
                        status="ACTIVE",
                    )
                    session.add(row)
                    await session.flush()
                    actor_id = row.id
        yield actor_id
    finally:
        async with session_factory() as session:
            async with session.begin():
                # The shared actor is FK-referenced by any Service a test created
                # (service_definition.created_by, ADR-052). A test that fails
                # before its own teardown must not leave the shared fixture
                # undeletable, so clean dependants first, then the actor.
                dangling = (
                    await session.scalars(
                        select(ServiceDefinitionModel.id).where(
                            ServiceDefinitionModel.created_by == actor_id
                        )
                    )
                ).all()
                for service_id in dangling:
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
                await session.execute(
                    delete(PlatformUserModel).where(PlatformUserModel.user_key == TEST_ACTOR_KEY)
                )
        await engine.dispose()
