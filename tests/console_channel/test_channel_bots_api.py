import re
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BotAccount

from console_channel.conftest import CHANNEL, ChannelContext

BOTS_URL = "/internal/channel/bots"
REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ITEM_KEYS = {"bot_account_id", "bot_id", "secret", "agent_id", "enabled", "channel"}


def _headers(channel: ChannelContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or channel.tenant_id}


async def _bots(
    client: AsyncClient,
    channel: ChannelContext,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    response = await client.get(BOTS_URL, headers=_headers(channel, tenant_id))
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    data: dict[str, Any] = body["data"]
    return data


async def test_lists_only_enabled_bots(client: AsyncClient, channel: ChannelContext) -> None:
    data = await _bots(client, channel)
    assert data["revision"] == (await _bots(client, channel))["revision"]
    assert REVISION_PATTERN.match(data["revision"]) is not None
    items = data["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item.keys()) == ITEM_KEYS
    async with get_session_factory()() as session:
        account = await session.scalar(
            sa.select(BotAccount).where(BotAccount.bot_id == channel.bot_id)
        )
    assert account is not None
    assert item["bot_account_id"] == str(account.id)
    assert item["bot_id"] == channel.bot_id
    assert item["channel"] == CHANNEL, "快照条目必须携带实际通道（Console 按行输出）"
    assert item["secret"] == channel.secret
    assert item["agent_id"] == str(channel.agent_id)
    assert item["enabled"] is True
    returned_bot_ids = {entry["bot_id"] for entry in items}
    assert channel.disabled_bot_id not in returned_bot_ids
    assert channel.deleted_bot_id not in returned_bot_ids


async def test_revision_changes_when_data_changes(
    client: AsyncClient, channel: ChannelContext
) -> None:
    first = await _bots(client, channel)
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(BotAccount)
            .where(
                BotAccount.tenant_id == channel.tenant_id,
                BotAccount.bot_id == channel.bot_id,
            )
            .values(secret="wecom-secret-rotated")
        )
        await session.commit()
    second = await _bots(client, channel)
    assert second["revision"] != first["revision"]
    assert second["items"][0]["secret"] == "wecom-secret-rotated"


async def test_tenant_isolation_excludes_other_tenant_bots(
    client: AsyncClient, channel: ChannelContext
) -> None:
    other = await _bots(client, channel, channel.other_tenant_id)
    returned_bot_ids = {entry["bot_id"] for entry in other["items"]}
    assert returned_bot_ids == {channel.other_bot_id}
    assert channel.bot_id not in returned_bot_ids


async def test_channel_filter_excludes_other_channel_secrets(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """`channels` 过滤是安全边界：Gateway 只取 WEBCHAT，绝不能看到企微 secret。"""
    webchat_bot_id = f"{channel.bot_id}-webchat"
    async with get_session_factory()() as session:
        session.add(
            BotAccount(
                tenant_id=channel.tenant_id,
                channel="WEBCHAT",
                name="WebChat entry",
                bot_id=webchat_bot_id,
                secret=None,
                agent_id=channel.agent_id,
                enabled=True,
            )
        )
        await session.commit()

    response = await client.get(
        BOTS_URL,
        headers=_headers(channel),
        params={"channels": "WEBCHAT"},
    )
    assert response.status_code == 200
    data = response.json()["data"]

    assert {entry["bot_id"] for entry in data["items"]} == {webchat_bot_id}
    assert all(entry["channel"] == "WEBCHAT" for entry in data["items"])
    assert all(entry["secret"] is None for entry in data["items"])

    # revision 也按通道集合收敛：同一个过滤集合稳定，换集合会变
    assert data["revision"] == (
        await client.get(
            BOTS_URL, headers=_headers(channel), params={"channels": "WEBCHAT"}
        )
    ).json()["data"]["revision"]
    wecom = await client.get(BOTS_URL, headers=_headers(channel), params={"channels": "WECOM"})
    assert {entry["bot_id"] for entry in wecom.json()["data"]["items"]} == {channel.bot_id}


async def test_include_secret_false_never_sends_bot_secrets(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """请求面（Gateway web 角色）不需要连接凭据：通道对也不下发 secret。"""
    response = await client.get(
        BOTS_URL,
        headers=_headers(channel),
        params={"channels": CHANNEL, "include_secret": "false"},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items and all(entry["secret"] is None for entry in items)
