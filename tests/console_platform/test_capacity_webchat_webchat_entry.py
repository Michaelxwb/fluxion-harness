"""[B-246] WEBCHAT 稳定入口控制面与共享快照类型（真实 Console ASGI + 真实 PostgreSQL）。

真实边界（业务路径不 mock）：
- **真实 Console 应用服务/HTTP**：ASGI 直连真实路由与依赖，真实登录会话裁决 ADMIN/CSRF，
  入口归属与租户隔离由真实服务层谓词裁决；
- **真实 PostgreSQL**：`control.bot_account` 行经真实事务写入并回读，`uq_bot_account_bot_id`
  的跨租户全局唯一、停用/软删过滤都由数据库与真实查询裁决。跨租户对照行直接落库，
  证明"不可见"来自租户谓词而不是"数据不存在"。

关键断言（Acceptance Contract B-246）：
1. WEBCHAT 稳定入口无需企微 secret（也不接受企微凭据）；
2. 创建后不原地改绑 Agent——改绑使用新入口 ID；
3. 停用保留历史归属：行、入口 ID 与 agent_id 不变，仅从启用快照消失；
4. Agent 0..N 通道账号能力保留，本轮企微仍按"必须带 secret"的单机器人基线验收；
5. 共享快照类型 `BotSnapshotItem.channel` 按实际通道输出（Gateway 与 Console 共用）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BotAccount
from muad_console_platform.infrastructure.models.control import AgentDefinition
from muad_contracts import BotSnapshotResponse
from sqlalchemy import select, text

from console_platform.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (夹具注册)

WECOM = "WECOM"
WEBCHAT = "WEBCHAT"
BOTS_URL = "/internal/channel/bots"
#: 快照条目的线上形状：`channel` 是本任务新增的共享字段（Gateway 与 Console 共用）。
SNAPSHOT_ITEM_KEYS = {"bot_account_id", "bot_id", "secret", "agent_id", "enabled", "channel"}


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _channels_url(agent_id: str) -> str:
    return f"/api/v1/agents/{agent_id}/channels"


async def _make_agent(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": _unique("webchat-agent"),
            "name": "Webchat Entry Agent",
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
    *,
    channel: str,
    bot_id: str,
    secret: str | None = None,
    config: dict[str, Any] | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "channel": channel,
        "name": f"Entry {bot_id[:16]}",
        "bot_id": bot_id,
    }
    if secret is not None:
        payload["secret"] = secret
    if config is not None:
        payload["config"] = config
    return await client.post(_channels_url(agent_id), json=payload, headers=_headers(tenant))


async def _list_channels(
    client: AsyncClient, tenant: TenantContext, agent_id: str
) -> dict[str, Any]:
    response = await client.get(_channels_url(agent_id), headers=_headers(tenant))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _snapshot(client: AsyncClient, tenant_id: str) -> dict[str, Any]:
    response = await client.get(BOTS_URL, headers=service_headers(tenant_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    return body["data"]


async def _bot_row(bot_id: str) -> BotAccount | None:
    async with get_session_factory()() as session:
        return await session.scalar(select(BotAccount).where(BotAccount.bot_id == bot_id))


@dataclass(frozen=True)
class ForeignEntry:
    """其他租户的入口对照行：直接落库，用于证明跨租户不可见来自查询谓词。"""

    tenant_id: str
    agent_id: uuid.UUID
    bot_id: str


@asynccontextmanager
async def _foreign_webchat_entry(model_id: uuid.UUID) -> AsyncIterator[ForeignEntry]:
    tenant_id = f"test-{uuid.uuid4()}"
    bot_id = _unique("webchat-foreign")
    session_factory = get_session_factory()
    async with session_factory() as session:
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique("foreign-agent"),
            name="Foreign Agent",
            instructions="inst",
            model_id=model_id,
        )
        session.add(agent)
        await session.flush()
        session.add(
            BotAccount(
                tenant_id=tenant_id,
                channel=WEBCHAT,
                name="Foreign Webchat Entry",
                bot_id=bot_id,
                secret=None,
                agent_id=agent.id,
                enabled=True,
            )
        )
        await session.commit()
        entry = ForeignEntry(tenant_id=tenant_id, agent_id=agent.id, bot_id=bot_id)
    try:
        yield entry
    finally:
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM control.bot_account WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.commit()


async def test_b246_webchat_entry_needs_no_wecom_secret(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：WEBCHAT 稳定入口无需企微 secret；企微凭据与未知通道被明确拒绝。"""
    agent_id = await _make_agent(client, tenant)
    bot_id = _unique("webchat")
    origins = ["https://chat.example.com"]

    created = await _add_channel(
        client, tenant, agent_id, channel=WEBCHAT, bot_id=bot_id,
        config={"allowed_origins": origins},
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["bot_id"] == bot_id
    assert data["agent_id"] == agent_id
    assert data["enabled"] is True
    assert data["last_connected_at"] is None
    assert "secret" not in data, "WEBCHAT 入口响应不得出现企微 secret 字段"

    row = await _bot_row(bot_id)
    assert row is not None
    assert row.channel == WEBCHAT
    assert row.secret is None, "WEBCHAT 稳定入口不落企微凭据"
    assert str(row.agent_id) == agent_id

    listed = await _list_channels(client, tenant, agent_id)
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["channel"] == WEBCHAT
    assert item["secret_configured"] is False
    assert item["config"]["allowed_origins"] == origins

    with_secret = await _add_channel(
        client, tenant, agent_id, channel=WEBCHAT, bot_id=_unique("webchat"),
        secret="wecom-secret-should-be-rejected",
    )
    assert with_secret.status_code == 422
    assert with_secret.json()["code"] == "COMMON_VALIDATION_ERROR"

    unknown = await _add_channel(client, tenant, agent_id, channel="SLACK", bot_id=_unique("x"))
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "COMMON_VALIDATION_ERROR"

    assert (await _list_channels(client, tenant, agent_id))["total"] == 1


async def test_b246_webchat_entry_agent_is_not_rebindable_in_place(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：创建后不原地改绑 Agent（也不原地换通道）；改绑使用新入口 ID。"""
    first_agent = await _make_agent(client, tenant)
    second_agent = await _make_agent(client, tenant)
    bot_id = _unique("webchat")
    created = await _add_channel(client, tenant, first_agent, channel=WEBCHAT, bot_id=bot_id)
    assert created.status_code == 200, created.text
    channel_account_id = created.json()["data"]["channel_account_id"]

    rebind = await client.put(
        f"{_channels_url(first_agent)}/{channel_account_id}",
        json={"agent_id": second_agent},
        headers=_headers(tenant),
    )
    assert rebind.status_code == 422
    assert rebind.json()["code"] == "COMMON_VALIDATION_ERROR"

    channel_swap = await client.put(
        f"{_channels_url(first_agent)}/{channel_account_id}",
        json={"channel": WECOM},
        headers=_headers(tenant),
    )
    assert channel_swap.status_code == 422

    row = await _bot_row(bot_id)
    assert row is not None
    assert str(row.agent_id) == first_agent, "原地改绑被拒后归属不得漂移"
    assert row.channel == WEBCHAT

    rebound_bot_id = _unique("webchat")
    rebound = await _add_channel(
        client, tenant, second_agent, channel=WEBCHAT, bot_id=rebound_bot_id
    )
    assert rebound.status_code == 200, rebound.text
    assert rebound.json()["data"]["channel_account_id"] != channel_account_id

    first_list = await _list_channels(client, tenant, first_agent)
    second_list = await _list_channels(client, tenant, second_agent)
    assert [item["bot_id"] for item in first_list["items"]] == [bot_id]
    assert [item["bot_id"] for item in second_list["items"]] == [rebound_bot_id]
    assert all(item["agent_id"] == first_agent for item in first_list["items"])
    assert all(item["agent_id"] == second_agent for item in second_list["items"])


async def test_b246_disable_keeps_entry_and_historical_attribution(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：停用保留历史归属——入口行/入口 ID/agent_id 不变，仅从启用快照消失。"""
    agent_id = await _make_agent(client, tenant)
    bot_id = _unique("webchat")
    created = await _add_channel(client, tenant, agent_id, channel=WEBCHAT, bot_id=bot_id)
    assert created.status_code == 200, created.text
    channel_account_id = created.json()["data"]["channel_account_id"]

    enabled_snapshot = await _snapshot(client, tenant.tenant_id)
    assert [item["bot_id"] for item in enabled_snapshot["items"]] == [bot_id]

    disabled = await client.put(
        f"{_channels_url(agent_id)}/{channel_account_id}",
        json={"enabled": False},
        headers=_headers(tenant),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["enabled"] is False

    listed = await _list_channels(client, tenant, agent_id)
    assert listed["total"] == 1, "停用不删除入口：控制面仍可查询"
    item = listed["items"][0]
    assert item["channel_account_id"] == channel_account_id
    assert item["enabled"] is False
    assert item["agent_id"] == agent_id, "停用保留历史归属的 Agent"

    row = await _bot_row(bot_id)
    assert row is not None
    assert row.is_deleted is False
    assert str(row.id) == channel_account_id
    assert str(row.agent_id) == agent_id

    assert (await _snapshot(client, tenant.tenant_id))["total"] == 0, "停用入口不参与启用快照"

    reenabled = await client.put(
        f"{_channels_url(agent_id)}/{channel_account_id}",
        json={"enabled": True},
        headers=_headers(tenant),
    )
    assert reenabled.status_code == 200, reenabled.text
    restored = await _snapshot(client, tenant.tenant_id)
    assert [item["bot_id"] for item in restored["items"]] == [bot_id]
    assert restored["items"][0]["channel"] == WEBCHAT


async def test_b246_agent_keeps_zero_to_n_accounts_and_wecom_requires_secret(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：Agent 保留 0..N 通道账号能力；本轮企微仍按必须带 secret 的单机器人基线。"""
    agent_id = await _make_agent(client, tenant)
    assert (await _list_channels(client, tenant, agent_id))["total"] == 0

    wecom_bot_id = _unique("wecom")
    missing_secret = await _add_channel(
        client, tenant, agent_id, channel=WECOM, bot_id=wecom_bot_id
    )
    assert missing_secret.status_code == 422
    assert missing_secret.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _bot_row(wecom_bot_id) is None, "校验失败的企微入口不得落库"

    secret = "wecom-secret-single-bot-baseline"
    wecom = await _add_channel(
        client, tenant, agent_id, channel=WECOM, bot_id=wecom_bot_id, secret=secret
    )
    assert wecom.status_code == 200, wecom.text
    assert secret not in wecom.text, "企微 secret 不得回显"
    row = await _bot_row(wecom_bot_id)
    assert row is not None and row.secret == secret

    for _ in range(2):
        webchat = await _add_channel(
            client, tenant, agent_id, channel=WEBCHAT, bot_id=_unique("webchat")
        )
        assert webchat.status_code == 200, webchat.text

    listed = await _list_channels(client, tenant, agent_id)
    assert listed["total"] == 3
    assert [item["channel"] for item in listed["items"]] == [WECOM, WEBCHAT, WEBCHAT]
    assert all(item["agent_id"] == agent_id for item in listed["items"])
    assert [item["secret_configured"] for item in listed["items"]] == [True, False, False]


async def test_b246_entry_key_unique_and_cross_tenant_invisible(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：入口键（bot_id）跨通道/跨租户全局唯一，且跨租户入口不可见、存在性不泄露。"""
    agent_id = await _make_agent(client, tenant)
    other_agent = await _make_agent(client, tenant)
    bot_id = _unique("webchat")
    created = await _add_channel(client, tenant, agent_id, channel=WEBCHAT, bot_id=bot_id)
    assert created.status_code == 200, created.text

    duplicate = await _add_channel(
        client, tenant, other_agent, channel=WEBCHAT, bot_id=bot_id
    )
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body["code"] == "BOT_ID_EXISTS"
    assert bot_id in str(body.get("data") or "") + str(body.get("msg") or "")

    cross_channel = await _add_channel(client, tenant, other_agent, channel=WECOM, bot_id=bot_id,
                                       secret="wecom-secret-cross-channel")
    assert cross_channel.status_code == 409, "入口键是同一命名空间：跨通道复用同样冲突"
    assert cross_channel.json()["code"] == "BOT_ID_EXISTS"
    assert (await _list_channels(client, tenant, other_agent))["total"] == 0

    async with _foreign_webchat_entry(tenant.model_id) as foreign:
        assert (await _list_channels(client, tenant, agent_id))["total"] == 1

        own_snapshot = await _snapshot(client, tenant.tenant_id)
        assert [item["bot_id"] for item in own_snapshot["items"]] == [bot_id]

        foreign_snapshot = await _snapshot(client, foreign.tenant_id)
        assert [item["bot_id"] for item in foreign_snapshot["items"]] == [foreign.bot_id]
        assert foreign_snapshot["items"][0]["channel"] == WEBCHAT
        assert foreign_snapshot["items"][0]["secret"] is None

        listed_foreign = await client.get(
            _channels_url(str(foreign.agent_id)), headers=_headers(tenant)
        )
        assert listed_foreign.status_code == 404
        assert listed_foreign.json()["code"] == "AGENT_NOT_FOUND"

        created_foreign = await _add_channel(
            client, tenant, str(foreign.agent_id), channel=WEBCHAT, bot_id=_unique("webchat")
        )
        assert created_foreign.status_code == 404
        assert created_foreign.json()["code"] == "AGENT_NOT_FOUND"


async def test_b246_snapshot_item_carries_actual_channel(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-246：共享快照条目携带实际通道（WECOM/WEBCHAT 并存），序列化形状与契约一致。"""
    agent_id = await _make_agent(client, tenant)
    wecom_bot_id = _unique("wecom")
    webchat_bot_id = _unique("webchat")
    secret = "wecom-secret-snapshot-channel"
    wecom = await _add_channel(
        client, tenant, agent_id, channel=WECOM, bot_id=wecom_bot_id, secret=secret
    )
    assert wecom.status_code == 200, wecom.text
    webchat = await _add_channel(
        client, tenant, agent_id, channel=WEBCHAT, bot_id=webchat_bot_id
    )
    assert webchat.status_code == 200, webchat.text

    data = await _snapshot(client, tenant.tenant_id)
    assert data["total"] == 2
    assert data["page"] == 1 and data["page_size"] == 20
    items = {item["bot_id"]: item for item in data["items"]}
    assert set(items) == {wecom_bot_id, webchat_bot_id}
    for item in items.values():
        assert set(item.keys()) == SNAPSHOT_ITEM_KEYS, "快照条目形状（含 channel）是共享契约"
    assert items[wecom_bot_id]["channel"] == WECOM
    assert items[wecom_bot_id]["secret"] == secret
    assert items[webchat_bot_id]["channel"] == WEBCHAT
    assert items[webchat_bot_id]["secret"] is None

    contract = BotSnapshotResponse.model_validate(data)
    assert {item.bot_id: item.channel for item in contract.items} == {
        wecom_bot_id: WECOM,
        webchat_bot_id: WEBCHAT,
    }
