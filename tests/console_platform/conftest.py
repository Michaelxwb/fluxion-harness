import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import ModelDefinition
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "control"
AGENT_TABLE = "agent_definition"
MODEL_TABLE = "model_definition"
AUTH_TABLES = ("console_account", "console_session")
ADMIN_PASSWORD = "console-admin-password"


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
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table, schema=SCHEMA)
                    for table in (AGENT_TABLE, MODEL_TABLE, *AUTH_TABLES)
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
            skill_ids = list(
                await session.scalars(
                    text("SELECT id FROM control.skill WHERE tenant_id = :tenant_id").bindparams(
                        tenant_id=tenant_id
                    )
                )
            )
            for statement in (
                "DELETE FROM control.agent_skill_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_mcp_binding WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_access_grant WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.bot_account WHERE agent_id IN "
                "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
                "DELETE FROM control.skill_user_grant WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.skill_artifact WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.skill WHERE tenant_id = :tenant_id",
                "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
                "DELETE FROM control.mcp_user_grant WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.mcp_server WHERE tenant_id = :tenant_id",
                "DELETE FROM control.bind_code WHERE platform_user_id IN "
                "(SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.channel_identity WHERE platform_user_id IN "
                "(SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
                "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant_id})
            await session.commit()
        artifact_root = Path(SharedSettings().artifact_root)
        for skill_id in skill_ids:
            shutil.rmtree(artifact_root / "skills" / str(skill_id), ignore_errors=True)


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
