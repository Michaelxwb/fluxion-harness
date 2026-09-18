"""S-02 E2E：IM Gateway（真实 HTTP 客户端）→ Console bind API → PostgreSQL。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application.channel_service import hash_bind_code
from muad_contracts import ChannelBindRequest
from muad_im_gateway.application.console_client import ConsoleClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
async def console_server() -> AsyncIterator[str]:
    settings = SharedSettings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    port = _free_port()
    env = {**os.environ, "DATABASE_URL": settings.database_url}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "muad_console_platform.main:app",
            "--app-dir",
            "apps/console-platform/backend/src",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"{BASE_URL}:{port}"
    try:
        deadline = time.monotonic() + 30
        async with httpx.AsyncClient(base_url=url, timeout=2) as client:
            while True:
                with contextlib.suppress(httpx.HTTPError):
                    response = await client.get("/healthz")
                    if response.status_code == 200:
                        break
                if time.monotonic() > deadline:
                    raise TimeoutError("console server did not become ready")
                await asyncio.sleep(0.3)
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)


@pytest.fixture()
async def engine() -> AsyncIterator[AsyncEngine]:
    settings = SharedSettings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    yield engine
    await engine.dispose()


async def test_s02_gateway_bind_creates_identity_without_grant(
    console_server: str, engine: AsyncEngine
) -> None:
    settings = SharedSettings()
    tenant_id = settings.default_tenant_id
    suffix = uuid.uuid4().hex[:8]
    user_id, model_id, agent_id, bot_account_id = (uuid.uuid4() for _ in range(4))
    bind_code_plain = f"E2E{suffix.upper()}"
    bind_code_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name) "
                "VALUES (:id, :tenant_id, :user_code, 'Gateway Bind User')"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": f"bind-e2e-{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO control.model_definition "
                "(id, tenant_id, key, name, model_id, base_url, enabled) "
                "VALUES (:id, :tenant_id, :key, 'Bind E2E Model', 'gpt-4o-mini', "
                "'https://api.example.com/v1', true)"
            ),
            {"id": model_id, "tenant_id": tenant_id, "key": f"bind-model-{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO control.agent_definition "
                "(id, tenant_id, key, name, instructions, model_id, runtime_config_json, enabled, revision) "
                "VALUES (:id, :tenant_id, :key, 'Bind E2E Agent', 'help', :model_id, '{}'::jsonb, true, 1)"
            ),
            {"id": agent_id, "tenant_id": tenant_id, "key": f"bind-agent-{suffix}", "model_id": model_id},
        )
        await connection.execute(
            text(
                "INSERT INTO control.bot_account "
                "(id, tenant_id, channel, name, bot_id, secret_ref, agent_id) "
                "VALUES (:id, :tenant_id, 'WECOM', 'Bind E2E Bot', :bot_id, 'secret://e2e/bot', :agent_id)"
            ),
            {
                "id": bot_account_id,
                "tenant_id": tenant_id,
                "bot_id": f"bind-bot-{suffix}",
                "agent_id": agent_id,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO control.bind_code "
                "(id, tenant_id, platform_user_id, code_hash, status, expires_at, created_by) "
                "VALUES (:id, :tenant_id, :user_id, :code_hash, 'ACTIVE', "
                "now() + interval '10 minutes', :created_by)"
            ),
            {
                "id": bind_code_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "code_hash": hash_bind_code(bind_code_plain),
                "created_by": uuid.uuid4(),
            },
        )

    try:
        client = ConsoleClient(console_server)
        try:
            response = await client.bind(
                ChannelBindRequest(
                    channel="WECOM",
                    bot_id=f"bind-bot-{suffix}",
                    external_user_id=f"external-{suffix}",
                    bind_code=bind_code_plain,
                ),
                tenant_id,
            )
        finally:
            await client.aclose()

        assert response.bound is True
        assert response.platform_user_id == user_id

        async with engine.connect() as connection:
            identity = (
                await connection.execute(
                    text(
                        "SELECT platform_user_id FROM control.channel_identity "
                        "WHERE tenant_id = :tenant_id AND platform_user_id = :user_id AND is_deleted = false"
                    ),
                    {"tenant_id": tenant_id, "user_id": user_id},
                )
            ).all()
            code_status = await connection.scalar(
                text("SELECT status FROM control.bind_code WHERE id = :id"), {"id": bind_code_id}
            )
            grant_count = await connection.scalar(
                text("SELECT count(*) FROM control.agent_access_grant WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        assert len(identity) == 1
        assert code_status == "USED"
        assert grant_count == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM control.channel_identity "
                    "WHERE tenant_id = :tenant_id AND platform_user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            await connection.execute(
                text("DELETE FROM control.bind_code WHERE id = :id"), {"id": bind_code_id}
            )
            await connection.execute(
                text("DELETE FROM control.bot_account WHERE id = :id"), {"id": bot_account_id}
            )
            await connection.execute(
                text("DELETE FROM control.agent_definition WHERE id = :id"), {"id": agent_id}
            )
            await connection.execute(
                text("DELETE FROM control.model_definition WHERE id = :id"), {"id": model_id}
            )
            await connection.execute(
                text("DELETE FROM control.platform_user WHERE id = :id"), {"id": user_id}
            )
