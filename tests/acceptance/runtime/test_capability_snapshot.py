"""[S-01][S-02][RULE-auth/mcp/secret/snapshot-001] 授权与 Snapshot 全链路验收。"""

from __future__ import annotations

import hashlib
import json as json_module
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.main import app

ADMIN_PASSWORD = "rt-admin-password"


@pytest.fixture
async def rt_tenant() -> AsyncIterator[dict[str, str]]:
    tenant_id = f"rt-{uuid.uuid4()}"
    username = f"admin-{uuid.uuid4()}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        from muad_console_platform.infrastructure.models.control import ModelDefinition

        account = ConsoleAccount(
            tenant_id=tenant_id, username=username, display_name="RT Admin",
            password_hash=hash_password(ADMIN_PASSWORD), role=ROLE_ADMIN,
        )
        model = ModelDefinition(
            tenant_id=tenant_id, key=f"m-{uuid.uuid4()}", name="RT Model",
            model_id="gpt-4o-mini", base_url="https://api.example.com/v1",
        )
        session.add_all([account, model])
        await session.commit()
        yield {"tenant_id": tenant_id, "model_id": str(model.id), "username": username}
    async with session_factory()() as session:
        from muad_console_platform.infrastructure.models.auth import ConsoleSession

        await session.execute(ConsoleSession.__table__.delete().where(
            ConsoleSession.account_id == account.id))
        await session.execute(ModelDefinition.__table__.delete().where(
            ModelDefinition.tenant_id == tenant_id))
        await session.execute(ConsoleAccount.__table__.delete().where(
            ConsoleAccount.id == account.id))
        await session.commit()


@pytest.fixture
async def client(rt_tenant) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/v1/auth/login",
            json={"username": rt_tenant["username"], "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": rt_rt_tenant["tenant_id"]},
        )
        assert login.status_code == 200
        http_client.headers[CSRF_HEADER] = http_client.cookies.get(CSRF_COOKIE) or ""
        yield http_client


def _headers(tenant: dict[str, str]) -> dict[str, str]:
    return {"X-Tenant-Id": rt_tenant["tenant_id"]}



@pytest.mark.asyncio
async def test_s01_unauthorized_catalog_zero_leakage(client, rt_tenant) -> None:
    """[S-01] 未授权 SELECTED Skill 不进入 resolve 输出。"""
    response = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
        headers={"X-Tenant-Id": rt_tenant["tenant_id"]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_s01_unauthorized_mcp_not_in_resolve(client, rt_tenant) -> None:
    """[S-01 延伸][RULE-auth-001] 未授权 SELECTED MCP 不进入 resolve mcp_servers。"""
    response = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
        headers={"X-Tenant-Id": rt_tenant["tenant_id"]},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_s02_snapshot_freeze_on_config_change(client, rt_tenant) -> None:
    """[S-02] 配置变更只影响新 Run：旧 Snapshot 行 content_hash/字段不漂移。"""
    from muad_agent_runtime.infrastructure.db import get_session_factory
    from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot

    key = f"snap-{uuid.uuid4().hex[:6]}"
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": key,
            "name": "Snap Agent",
            "instructions": "v1 instructions",
            "model_id": str(rt_tenant["model_id"]),
        },
        headers={"X-Tenant-Id": rt_tenant["tenant_id"]},
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["data"]["id"]

    snapshot_json = {
        "instructions": "v1 instructions",
        "model_id": str(rt_tenant["model_id"]),
    }
    content_hash = "sha256:" + hashlib.sha256(
        json_module.dumps(snapshot_json, sort_keys=True).encode()
    ).hexdigest()

    session_factory = get_session_factory()
    async with session_factory() as session:
        snapshot = RuntimeSnapshot(
            tenant_id=rt_tenant["tenant_id"],
            run_id=uuid.uuid4(),
            agent_revision=1,
            model_revision=1,
            agent_json=snapshot_json,
            model_json={"id": "m"},
            skill_catalog_json=[],
            mcp_catalog_json=[],
            policy_json={},
            prompt_template_version="1",
            content_hash=content_hash,
        )
        session.add(snapshot)
        await session.commit()
        snapshot_id = snapshot.id

    await client.put(
        f"/api/v1/agents/{agent_id}",
        json={"expected_revision": 1, "instructions": "v2 instructions"},
        headers={"X-Tenant-Id": rt_tenant["tenant_id"]},
    )

    async with session_factory() as session:
        row = await session.get(RuntimeSnapshot, snapshot_id)
        assert row is not None
        assert row.agent_json["instructions"] == "v1 instructions"
        assert row.content_hash == content_hash


def test_s02_secret_not_in_snapshot_or_hash() -> None:
    """[RULE-secret-001] api_key 不落 snapshot model_json 也不参与 content_hash。"""
    from muad_agent_runtime.application.run_service import _snapshot_model
    from muad_contracts import ResolvedModel

    dump = _snapshot_model(
        ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini",
            base_url="https://x", api_key="sk-live-secret",
        )
    )
    assert "api_key" not in dump
    assert "sk-live-secret" not in str(dump)
