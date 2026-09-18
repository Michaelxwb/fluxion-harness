import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelDefinition,
)
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "control"
GUARD_TABLES = (
    "model_definition",
    "agent_definition",
    "agent_access_grant",
    "platform_user",
    "console_account",
    "console_session",
)
ADMIN_PASSWORD = "console-admin-password"

PLATFORM_USER = sa.table(
    "platform_user",
    sa.column("id", sa.Uuid()),
    sa.column("tenant_id", sa.String()),
    sa.column("user_code", sa.String()),
    sa.column("display_name", sa.String()),
    schema=SCHEMA,
)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    other_tenant_id: str
    actor_user_id: uuid.UUID
    other_actor_user_id: uuid.UUID
    model_id: uuid.UUID
    model_revision: int
    model_model_id: str
    model_base_url: str
    model_api_key: str
    model_params: dict[str, Any]
    disabled_model_id: uuid.UUID
    deleted_model_id: uuid.UUID
    agent_id: uuid.UUID
    agent_key: str
    agent_instructions: str
    agent_runtime_config: dict[str, Any]
    agent_revision: int
    disabled_agent_id: uuid.UUID
    deleted_agent_id: uuid.UUID
    ungranted_agent_id: uuid.UUID
    revoked_agent_id: uuid.UUID
    deleted_model_agent_id: uuid.UUID
    disabled_model_agent_id: uuid.UUID


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
async def tenant(database_guard: None) -> AsyncIterator[TenantContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    other_tenant_id = f"test-{uuid.uuid4()}"
    actor_user_id = uuid.uuid4()
    other_actor_user_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            sa.insert(PLATFORM_USER).values(
                [
                    {
                        "id": actor_user_id,
                        "tenant_id": tenant_id,
                        "user_code": _unique_key("user"),
                        "display_name": "Actor",
                    },
                    {
                        "id": other_actor_user_id,
                        "tenant_id": tenant_id,
                        "user_code": _unique_key("user"),
                        "display_name": "Other Actor",
                    },
                ]
            )
        )
        enabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Enabled Model",
            protocol="OPENAI",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            api_key="sk-happy",
            params_json={"temperature": 0.3},
            revision=5,
            enabled=True,
        )
        disabled_model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Disabled Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            enabled=False,
        )
        deleted_model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Deleted Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            is_deleted=True,
        )
        session.add_all([enabled_model, disabled_model, deleted_model])
        await session.flush()
        granted_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Granted Agent",
            instructions="You are a granted agent.",
            model_id=enabled_model.id,
            runtime_config={"temperature": 0.2},
            revision=3,
            enabled=True,
        )
        disabled_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Disabled Agent",
            instructions="You are a disabled agent.",
            model_id=enabled_model.id,
            enabled=False,
        )
        deleted_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Deleted Agent",
            instructions="You are a deleted agent.",
            model_id=enabled_model.id,
            is_deleted=True,
        )
        ungranted_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Ungranted Agent",
            instructions="You are an ungranted agent.",
            model_id=enabled_model.id,
        )
        revoked_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Revoked Agent",
            instructions="You are a revoked agent.",
            model_id=enabled_model.id,
        )
        deleted_model_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Deleted Model Agent",
            instructions="You reference a deleted model.",
            model_id=deleted_model.id,
        )
        disabled_model_agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Disabled Model Agent",
            instructions="You reference a disabled model.",
            model_id=disabled_model.id,
        )
        agents = [
            granted_agent,
            disabled_agent,
            deleted_agent,
            ungranted_agent,
            revoked_agent,
            deleted_model_agent,
            disabled_model_agent,
        ]
        session.add_all(agents)
        await session.flush()
        session.add_all(
            [
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=granted_agent.id,
                    granted_by=other_actor_user_id,
                ),
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=disabled_agent.id,
                    granted_by=other_actor_user_id,
                ),
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=revoked_agent.id,
                    granted_by=other_actor_user_id,
                    is_deleted=True,
                ),
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=deleted_model_agent.id,
                    granted_by=other_actor_user_id,
                ),
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=disabled_model_agent.id,
                    granted_by=other_actor_user_id,
                ),
            ]
        )
        await session.commit()
        context = TenantContext(
            tenant_id=tenant_id,
            other_tenant_id=other_tenant_id,
            actor_user_id=actor_user_id,
            other_actor_user_id=other_actor_user_id,
            model_id=enabled_model.id,
            model_revision=enabled_model.revision,
            model_model_id=enabled_model.model_id,
            model_base_url=enabled_model.base_url,
            model_api_key=enabled_model.api_key or "",
            model_params=enabled_model.params_json,
            disabled_model_id=disabled_model.id,
            deleted_model_id=deleted_model.id,
            agent_id=granted_agent.id,
            agent_key=granted_agent.key,
            agent_instructions=granted_agent.instructions,
            agent_runtime_config=granted_agent.runtime_config,
            agent_revision=granted_agent.revision,
            disabled_agent_id=disabled_agent.id,
            deleted_agent_id=deleted_agent.id,
            ungranted_agent_id=ungranted_agent.id,
            revoked_agent_id=revoked_agent.id,
            deleted_model_agent_id=deleted_model_agent.id,
            disabled_model_agent_id=disabled_model_agent.id,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM control.agent_access_grant WHERE agent_id IN "
                    "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)"
                ),
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
            await session.execute(
                text("DELETE FROM control.platform_user WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.commit()


@pytest.fixture
async def client(tenant: TenantContext) -> AsyncIterator[AsyncClient]:
    username = f"admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        account = ConsoleAccount(
            tenant_id=tenant.tenant_id,
            username=username,
            display_name="Console Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login_response = await http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": tenant.tenant_id},
        )
        assert login_response.status_code == 200
        csrf_token = http_client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        http_client.headers[CSRF_HEADER] = csrf_token
        yield http_client
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM control.console_session WHERE account_id = :account_id"),
            {"account_id": account_id},
        )
        await session.execute(
            text("DELETE FROM control.console_account WHERE id = :account_id"),
            {"account_id": account_id},
        )
        await session.commit()
