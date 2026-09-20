"""[S-03][E-02][E-06][B-03] IM 通道 CRUD（真实 HTTP + 真实 PostgreSQL）。"""

from __future__ import annotations

import uuid

import httpx
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _make_agent(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": f"ch-{uuid.uuid4().hex[:8]}",
            "name": "Channel Agent",
            "instructions": "inst",
            "model_id": str(tenant.model_id),
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"]


async def _add_channel(
    client: AsyncClient,
    tenant: TenantContext,
    agent_id: str,
    bot_id: str,
    *,
    secret: str = "wecom-secret-value",
) -> httpx.Response:
    return await client.post(
        f"/api/v1/agents/{agent_id}/channels",
        json={
            "channel": "WECOM",
            "name": f"Bot {bot_id[:8]}",
            "bot_id": bot_id,
            "secret": secret,
        },
        headers=_headers(tenant),
    )


async def _fetch_secret(bot_id: str) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            text("SELECT secret FROM control.bot_account WHERE bot_id = :bot_id"),
            {"bot_id": bot_id},
        )


async def test_s03_two_bots_same_agent_secret_persisted_not_echoed(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-03][B-03] 同 Agent 两个 bot；secret 明文落库；接口/审计不回显。"""
    agent_id = await _make_agent(client, tenant)
    bot_a = f"bot-a-{uuid.uuid4().hex[:8]}"
    bot_b = f"bot-b-{uuid.uuid4().hex[:8]}"

    first = await _add_channel(client, tenant, agent_id, bot_a, secret="secret-alpha-value")
    assert first.status_code == 200, first.text
    second = await _add_channel(client, tenant, agent_id, bot_b, secret="secret-beta-value")
    assert second.status_code == 200, second.text

    # 明文落 Owner 表
    assert await _fetch_secret(bot_a) == "secret-alpha-value"
    assert await _fetch_secret(bot_b) == "secret-beta-value"

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/channels", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 2
    for item in listed["items"]:
        for field in (
            "channel_account_id",
            "channel",
            "name",
            "bot_id",
            "secret_configured",
            "enabled",
            "create_time",
        ):
            assert field in item, f"缺字段 {field}"
        assert item["secret_configured"] is True
        assert "secret-alpha-value" not in str(item)
        assert "secret-beta-value" not in str(item)
        assert "agent_id" in item  # bot 指向同一 agent；无 Pod/实例字段
        assert "pod" not in str(item).lower()
        assert all(item["agent_id"] == agent_id for _ in [0])

    # 审计不含明文
    async with get_session_factory()() as session:
        audits = (
            await session.execute(
                text(
                    "SELECT after_json FROM control.config_audit_log "
                    "WHERE resource_type = 'AGENT' AND resource_id = :rid"
                ),
                {"rid": uuid.UUID(agent_id)},
            )
        ).all()
        assert "secret-alpha-value" not in str(audits)
        assert "secret-beta-value" not in str(audits)


async def test_e02_duplicate_bot_id_conflicts_with_message_args(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-02] bot_id 已被其他 Agent 占用 → BOT_ID_EXISTS（msg 带 bot_id），不重绑。"""
    agent_one = await _make_agent(client, tenant)
    agent_two = await _make_agent(client, tenant)
    bot_id = f"shared-{uuid.uuid4().hex[:8]}"

    first = await _add_channel(client, tenant, agent_one, bot_id)
    assert first.status_code == 200

    conflict = await _add_channel(client, tenant, agent_two, bot_id)
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["code"] == "BOT_ID_EXISTS"
    assert bot_id in str(body.get("data") or "") + str(body.get("msg") or "")

    # 占用关系不变：agent_one 仍持有该 bot
    listed = (
        await client.get(f"/api/v1/agents/{agent_one}/channels", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 1


async def test_e06_edit_or_remove_missing_channel_returns_bot_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-06] 编辑/移除不存在的 channel_account_id → BOT_NOT_FOUND，不修改数据。"""
    agent_id = await _make_agent(client, tenant)
    bot_id = f"kept-{uuid.uuid4().hex[:8]}"
    added = await _add_channel(client, tenant, agent_id, bot_id)
    assert added.status_code == 200

    missing_id = str(uuid.uuid4())
    edited = await client.put(
        f"/api/v1/agents/{agent_id}/channels/{missing_id}",
        json={"name": "Nope"},
        headers=_headers(tenant),
    )
    assert edited.status_code == 404
    assert edited.json()["code"] == "BOT_NOT_FOUND"

    removed = await client.delete(
        f"/api/v1/agents/{agent_id}/channels/{missing_id}", headers=_headers(tenant)
    )
    assert removed.status_code == 404
    assert removed.json()["code"] == "BOT_NOT_FOUND"

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/channels", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 1  # 数据未变


async def test_edit_rotates_secret_and_remove_is_soft_delete(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """编辑传 secret 即轮换；移除=软删除且不影响同 Agent 其他通道。"""
    agent_id = await _make_agent(client, tenant)
    bot_keep = f"keep-{uuid.uuid4().hex[:8]}"
    bot_rot = f"rot-{uuid.uuid4().hex[:8]}"
    await _add_channel(client, tenant, agent_id, bot_keep)
    added = await _add_channel(client, tenant, agent_id, bot_rot, secret="old-secret")
    channel_id = added.json()["data"]["channel_account_id"]

    edited = await client.put(
        f"/api/v1/agents/{agent_id}/channels/{channel_id}",
        json={"name": "Rotated", "secret": "new-secret", "enabled": False},
        headers=_headers(tenant),
    )
    assert edited.status_code == 200, edited.text
    assert await _fetch_secret(bot_rot) == "new-secret"
    assert "new-secret" not in edited.text

    removed = await client.delete(
        f"/api/v1/agents/{agent_id}/channels/{channel_id}", headers=_headers(tenant)
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["is_deleted"] is True

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/channels", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 1  # 其他通道不受影响
    assert listed["items"][0]["bot_id"] == bot_keep
