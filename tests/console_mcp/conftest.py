import threading
import time
import uuid
from collections.abc import AsyncIterator

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)
from muad_console_platform.main import app
from sqlalchemy import inspect

ADMIN_PASSWORD = "console-admin-password"

GUARD_TABLES = (
    "console_account",
    "console_session",
    "config_audit_log",
    "agent_definition",
    "model_definition",
    "platform_user",
    "mcp_server",
    "mcp_user_grant",
    "agent_mcp_binding",
    "skill_import_idempotency",
)


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    from muad_common import SharedSettings
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync: all(
                    inspect(sync).has_table(table, schema="control") for table in GUARD_TABLES
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def probe_url() -> AsyncIterator[str]:
    config = uvicorn.Config("tests.e2e.mcp_probe_app:app", host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True


@pytest.fixture
async def mcp_env(database_guard) -> AsyncIterator[dict[str, object]]:
    tenant_id = f"mcp-test-{uuid.uuid4()}"
    admin_username = f"admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        admin = ConsoleAccount(
            tenant_id=tenant_id,
            username=admin_username,
            display_name="Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="MCP Model",
            model_id="gpt-4o-mini",
            base_url="https://model.example.com/v1",
        )
        user = PlatformUser(
            tenant_id=tenant_id, user_code=f"user-{uuid.uuid4()}", display_name="Actor"
        )
        other_user = PlatformUser(
            tenant_id=tenant_id, user_code=f"user-{uuid.uuid4()}", display_name="Other"
        )
        session.add_all([admin, model, user, other_user])
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=f"agent-{uuid.uuid4()}",
            name="MCP Agent",
            model_id=model.id,
            instructions="You are a test agent.",
        )
        session.add(agent)
        await session.commit()
        yield {
            "tenant_id": tenant_id,
            "admin_username": admin_username,
            "admin_id": admin.id,
            "agent_id": agent.id,
            "actor_user_id": user.id,
            "other_user_id": other_user.id,
        }
        from muad_console_platform.infrastructure.models.mcp import (
            AgentMcpBinding,
            McpServer,
            McpUserGrant,
        )

        await session.execute(
            McpUserGrant.__table__.delete().where(McpUserGrant.user_id.in_([user.id, other_user.id]))
        )
        server_rows = (
            await session.execute(
                McpServer.__table__.select().where(McpServer.tenant_id == tenant_id)
            )
        ).all()
        server_ids = [row.id for row in server_rows]
        if server_ids:
            await session.execute(
                AgentMcpBinding.__table__.delete().where(AgentMcpBinding.mcp_server_id.in_(server_ids))
            )
            await session.execute(
                McpServer.__table__.delete().where(McpServer.id.in_(server_ids))
            )
        from muad_console_platform.infrastructure.models.auth import ConsoleSession

        await session.execute(
            ConsoleSession.__table__.delete().where(ConsoleSession.account_id == admin.id)
        )
        await session.delete(agent)
        await session.delete(model)
        await session.delete(user)
        await session.delete(other_user)
        await session.delete(admin)
        await session.commit()


@pytest.fixture
async def client(mcp_env: dict[str, object]) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as active:
        login = await active.post(
            "/api/v1/auth/login",
            json={"username": mcp_env["admin_username"], "password": ADMIN_PASSWORD},
            headers=tenant_headers(mcp_env),
        )
        assert login.status_code == 200, login.text
        csrf = login.cookies.get(CSRF_COOKIE)
        active.headers[CSRF_HEADER] = csrf or ""
        yield active


def tenant_headers(env: dict[str, object]) -> dict[str, str]:
    return {"X-Tenant-Id": str(env["tenant_id"])}


async def create_mcp(
    client: AsyncClient,
    env: dict[str, object],
    *,
    endpoint: str,
    key: str | None = None,
    extra: dict[str, object] | None = None,
) -> object:
    payload: dict[str, object] = {
        "name": "Probe MCP",
        "key": key or f"mcp-{uuid.uuid4()}",
        "endpoint": endpoint,
    }
    payload.update(extra or {})
    return await client.post(
        "/api/v1/mcp-servers", json=payload, headers=tenant_headers(env)
    )


@pytest.fixture
async def env(mcp_env: dict[str, object]) -> dict[str, object]:
    """TASK-003+ 测试使用别名 fixture。"""
    return mcp_env
