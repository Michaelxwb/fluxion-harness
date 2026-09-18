import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.channel_service import build_identity_key, hash_bind_code
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.channel import (
    BIND_CODE_STATUS_ACTIVE,
    BIND_CODE_STATUS_USED,
    BindCode,
    BotAccount,
    ChannelIdentity,
)
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelDefinition,
)
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

SCHEMA = "control"
CHANNEL = "WECOM"
GUARD_TABLES = (
    "platform_user",
    "bot_account",
    "channel_identity",
    "bind_code",
    "agent_definition",
    "agent_access_grant",
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
    sa.column("status", sa.String()),
    schema=SCHEMA,
)


@dataclass(frozen=True)
class ChannelContext:
    tenant_id: str
    other_tenant_id: str
    agent_id: uuid.UUID
    other_agent_id: uuid.UUID
    bot_id: str
    disabled_bot_id: str
    deleted_bot_id: str
    other_bot_id: str
    secret: str
    actor_user_id: uuid.UUID
    ungranted_user_id: uuid.UUID
    disabled_user_id: uuid.UUID
    bound_external_user_id: str
    ungranted_external_user_id: str
    disabled_external_user_id: str
    unbound_external_user_id: str
    valid_bind_code: str
    expired_bind_code: str
    used_bind_code: str
    unknown_bind_code: str
    other_tenant_bind_code: str


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
async def channel(database_guard: None) -> AsyncIterator[ChannelContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    other_tenant_id = f"test-{uuid.uuid4()}"
    actor_user_id = uuid.uuid4()
    ungranted_user_id = uuid.uuid4()
    disabled_user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    bot_id = _unique_key("bot")
    disabled_bot_id = _unique_key("bot")
    deleted_bot_id = _unique_key("bot")
    other_bot_id = _unique_key("bot")
    secret = f"wecom-secret-{bot_id}"
    bound_external_user_id = _unique_key("ext")
    ungranted_external_user_id = _unique_key("ext")
    disabled_external_user_id = _unique_key("ext")
    unbound_external_user_id = _unique_key("ext")
    valid_bind_code = _unique_key("code")
    expired_bind_code = _unique_key("code")
    used_bind_code = _unique_key("code")
    other_tenant_bind_code = _unique_key("code")
    now = datetime.now(UTC)
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
                        "status": "ACTIVE",
                    },
                    {
                        "id": ungranted_user_id,
                        "tenant_id": tenant_id,
                        "user_code": _unique_key("user"),
                        "display_name": "Ungranted",
                        "status": "ACTIVE",
                    },
                    {
                        "id": disabled_user_id,
                        "tenant_id": tenant_id,
                        "user_code": _unique_key("user"),
                        "display_name": "Disabled",
                        "status": "DISABLED",
                    },
                    {
                        "id": other_user_id,
                        "tenant_id": other_tenant_id,
                        "user_code": _unique_key("user"),
                        "display_name": "Other Tenant",
                        "status": "ACTIVE",
                    },
                ]
            )
        )
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Channel Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
        )
        other_model = ModelDefinition(
            tenant_id=other_tenant_id,
            key=_unique_key("model"),
            name="Other Channel Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
        )
        session.add_all([model, other_model])
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Channel Agent",
            instructions="You are a channel agent.",
            model_id=model.id,
        )
        other_agent = AgentDefinition(
            tenant_id=other_tenant_id,
            key=_unique_key("agent"),
            name="Other Channel Agent",
            instructions="You are another channel agent.",
            model_id=other_model.id,
        )
        session.add_all([agent, other_agent])
        await session.flush()
        enabled_bot = BotAccount(
            tenant_id=tenant_id,
            channel=CHANNEL,
            name="Enabled Bot",
            bot_id=bot_id,
            secret=secret,
            agent_id=agent.id,
        )
        disabled_bot = BotAccount(
            tenant_id=tenant_id,
            channel=CHANNEL,
            name="Disabled Bot",
            bot_id=disabled_bot_id,
            secret=f"wecom-secret-{disabled_bot_id}",
            agent_id=agent.id,
            enabled=False,
        )
        deleted_bot = BotAccount(
            tenant_id=tenant_id,
            channel=CHANNEL,
            name="Deleted Bot",
            bot_id=deleted_bot_id,
            secret=f"wecom-secret-{deleted_bot_id}",
            agent_id=agent.id,
            is_deleted=True,
        )
        other_bot = BotAccount(
            tenant_id=other_tenant_id,
            channel=CHANNEL,
            name="Other Tenant Bot",
            bot_id=other_bot_id,
            secret=f"wecom-secret-{other_bot_id}",
            agent_id=other_agent.id,
        )
        session.add_all([enabled_bot, disabled_bot, deleted_bot, other_bot])
        await session.flush()
        session.add_all(
            [
                ChannelIdentity(
                    tenant_id=tenant_id,
                    channel=CHANNEL,
                    identity_key=build_identity_key(CHANNEL, bot_id, bound_external_user_id),
                    external_user_id=bound_external_user_id,
                    bot_account_id=enabled_bot.id,
                    platform_user_id=actor_user_id,
                ),
                ChannelIdentity(
                    tenant_id=tenant_id,
                    channel=CHANNEL,
                    identity_key=build_identity_key(CHANNEL, bot_id, ungranted_external_user_id),
                    external_user_id=ungranted_external_user_id,
                    bot_account_id=enabled_bot.id,
                    platform_user_id=ungranted_user_id,
                ),
                ChannelIdentity(
                    tenant_id=tenant_id,
                    channel=CHANNEL,
                    identity_key=build_identity_key(CHANNEL, bot_id, disabled_external_user_id),
                    external_user_id=disabled_external_user_id,
                    bot_account_id=enabled_bot.id,
                    platform_user_id=disabled_user_id,
                ),
                AgentAccessGrant(
                    user_id=actor_user_id,
                    agent_id=agent.id,
                    granted_by=actor_user_id,
                ),
                BindCode(
                    tenant_id=tenant_id,
                    platform_user_id=actor_user_id,
                    code_hash=hash_bind_code(valid_bind_code),
                    status=BIND_CODE_STATUS_ACTIVE,
                    expires_at=now + timedelta(minutes=10),
                    created_by=actor_user_id,
                ),
                BindCode(
                    tenant_id=tenant_id,
                    platform_user_id=actor_user_id,
                    code_hash=hash_bind_code(expired_bind_code),
                    status=BIND_CODE_STATUS_ACTIVE,
                    expires_at=now - timedelta(minutes=1),
                    created_by=actor_user_id,
                ),
                BindCode(
                    tenant_id=tenant_id,
                    platform_user_id=actor_user_id,
                    code_hash=hash_bind_code(used_bind_code),
                    status=BIND_CODE_STATUS_USED,
                    expires_at=now + timedelta(minutes=10),
                    used_at=now - timedelta(minutes=1),
                    created_by=actor_user_id,
                ),
                BindCode(
                    tenant_id=other_tenant_id,
                    platform_user_id=other_user_id,
                    code_hash=hash_bind_code(other_tenant_bind_code),
                    status=BIND_CODE_STATUS_ACTIVE,
                    expires_at=now + timedelta(minutes=10),
                    created_by=other_user_id,
                ),
            ]
        )
        await session.commit()
        context = ChannelContext(
            tenant_id=tenant_id,
            other_tenant_id=other_tenant_id,
            agent_id=agent.id,
            other_agent_id=other_agent.id,
            bot_id=bot_id,
            disabled_bot_id=disabled_bot_id,
            deleted_bot_id=deleted_bot_id,
            other_bot_id=other_bot_id,
            secret=secret,
            actor_user_id=actor_user_id,
            ungranted_user_id=ungranted_user_id,
            disabled_user_id=disabled_user_id,
            bound_external_user_id=bound_external_user_id,
            ungranted_external_user_id=ungranted_external_user_id,
            disabled_external_user_id=disabled_external_user_id,
            unbound_external_user_id=unbound_external_user_id,
            valid_bind_code=valid_bind_code,
            expired_bind_code=expired_bind_code,
            used_bind_code=used_bind_code,
            unknown_bind_code=_unique_key("code"),
            other_tenant_bind_code=other_tenant_bind_code,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            for tenant in (tenant_id, other_tenant_id):
                await session.execute(
                    text("DELETE FROM control.bind_code WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text("DELETE FROM control.channel_identity WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text("DELETE FROM control.bot_account WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text(
                        "DELETE FROM control.agent_access_grant WHERE agent_id IN "
                        "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)"
                    ),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text("DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text("DELETE FROM control.model_definition WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
                await session.execute(
                    text("DELETE FROM control.platform_user WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant},
                )
            await session.commit()


@pytest.fixture
async def client(channel: ChannelContext) -> AsyncIterator[AsyncClient]:
    username = f"admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        account = ConsoleAccount(
            tenant_id=channel.tenant_id,
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
            headers={"X-Tenant-Id": channel.tenant_id},
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
