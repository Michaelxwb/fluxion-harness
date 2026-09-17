import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ModelDefinition
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "control"
AGENT_TABLE = "agent_definition"
MODEL_TABLE = "model_definition"


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    model_id: uuid.UUID
    disabled_model_id: uuid.UUID


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync_connection: (
                    inspect(sync_connection).has_table(AGENT_TABLE, schema=SCHEMA)
                    and inspect(sync_connection).has_table(MODEL_TABLE, schema=SCHEMA)
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(database_guard: None) -> AsyncIterator[TenantContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        enabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Enabled Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            enabled=True,
        )
        disabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="Disabled Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            enabled=False,
        )
        session.add_all([enabled_model, disabled_model])
        await session.commit()
        context = TenantContext(
            tenant_id=tenant_id,
            model_id=enabled_model.id,
            disabled_model_id=disabled_model.id,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM control.model_definition WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
