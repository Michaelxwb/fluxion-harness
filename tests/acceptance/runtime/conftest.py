"""Runtime acceptance E2E fixtures：真实 Console HTTP + 真实 PostgreSQL。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from muad_common import SharedSettings

SCHEMA = "control"
ADMIN_PASSWORD = "console-admin-password"


@dataclass(frozen=True)
class AcceptanceTenant:
    tenant_id: str
    admin_username: str
    model_id: str
    agent_id: str


@pytest.fixture(scope="module")
async def _setup():
    settings = SharedSettings()
    if not settings.database_url:
        pytest.fail("DATABASE_URL not configured: Runtime E2E requires real PG")
    tenant_id = f"rt-e2e-{uuid.uuid4()}"
    admin_username = f"rt-admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        account = ConsoleAccount(
            tenant_id=tenant_id,
            username=admin_username,
            display_name="RT Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        from muad_console_platform.infrastructure.models.control import ModelDefinition

        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4()}",
            name="RT Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
            api_key="rt-e2e-key",
        )
        session.add_all([account, model])
        await session.commit()
        model_id = model.id
        account_id = account.id
    yield {"tenant_id": tenant_id, "admin_username": admin_username, "model_id": str(model_id)}
    async with session_factory() as session:
        from muad_console_platform.infrastructure.models.auth import ConsoleSession

        await session.execute(
            ConsoleSession.__table__.delete().where(ConsoleSession.account_id == account_id)
        )
        # agent_definition 引用 model_definition，先删前者
        await session.execute(
            text(f"DELETE FROM control.agent_definition WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        await session.execute(
            text(f"DELETE FROM control.config_audit_log WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        await session.execute(
            ModelDefinition.__table__.delete().where(ModelDefinition.tenant_id == tenant_id)
        )
        await session.execute(
            ConsoleAccount.__table__.delete().where(ConsoleAccount.id == account_id)
        )
        await session.commit()


@pytest.fixture
async def client(_setup) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/v1/auth/login",
            json={"username": _setup["admin_username"], "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": _setup["tenant_id"]},
        )
        assert login.status_code == 200, login.text
        csrf = http_client.cookies.get(CSRF_COOKIE)
        http_client.headers[CSRF_HEADER] = csrf
        yield http_client


@pytest.fixture
def tenant(_setup) -> dict[str, str]:
    return {"tenant_id": _setup["tenant_id"], "model_id": _setup["model_id"]}


def _headers(t: dict[str, str]) -> dict[str, str]:
    return {"X-Tenant-Id": t["tenant_id"]}
