import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import AgentDefinition, ModelDefinition
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "control"
GUARD_TABLES = (
    "console_account",
    "console_session",
    "config_audit_log",
    "agent_definition",
    "model_definition",
)

ADMIN_PASSWORD = "console-admin-password"
BUILDER_PASSWORD = "console-builder-password"


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    admin_id: uuid.UUID
    admin_username: str
    builder_id: uuid.UUID
    builder_username: str
    disabled_username: str
    model_id: uuid.UUID
    agent_id: uuid.UUID
    agent_key: str


def _unique_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table, schema=SCHEMA)
                    for table in GUARD_TABLES
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


@pytest.fixture
async def auth(database_guard: None) -> AsyncIterator[AuthContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    admin_username = _unique_key("admin")
    builder_username = _unique_key("builder")
    disabled_username = _unique_key("disabled")
    session_factory = get_session_factory()
    async with session_factory() as session:
        admin = ConsoleAccount(
            tenant_id=tenant_id,
            username=admin_username,
            display_name="Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        builder = ConsoleAccount(
            tenant_id=tenant_id,
            username=builder_username,
            display_name="Builder",
            password_hash=hash_password(BUILDER_PASSWORD),
            role=ROLE_BUILDER,
        )
        disabled = ConsoleAccount(
            tenant_id=tenant_id,
            username=disabled_username,
            display_name="Disabled",
            password_hash=hash_password(BUILDER_PASSWORD),
            role=ROLE_BUILDER,
            enabled=False,
        )
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Auth Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
        )
        session.add_all([admin, builder, disabled, model])
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Auth Agent",
            instructions="You are an auth agent.",
            model_id=model.id,
        )
        session.add(agent)
        await session.commit()
        context = AuthContext(
            tenant_id=tenant_id,
            admin_id=admin.id,
            admin_username=admin_username,
            builder_id=builder.id,
            builder_username=builder_username,
            disabled_username=disabled_username,
            model_id=model.id,
            agent_id=agent.id,
            agent_key=agent.key,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "DELETE FROM control.console_session WHERE account_id IN "
                    "(SELECT id FROM control.console_account WHERE tenant_id = :tenant_id)"
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM control.console_account WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
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


def tenant_headers(context: AuthContext) -> dict[str, str]:
    return {"X-Tenant-Id": context.tenant_id}


async def login(client: AsyncClient, context: AuthContext, username: str, password: str) -> Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=tenant_headers(context),
    )


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}


async def fetch_account(context: AuthContext, username: str) -> ConsoleAccount | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(ConsoleAccount).where(
                ConsoleAccount.tenant_id == context.tenant_id,
                ConsoleAccount.username == username,
            )
        )
