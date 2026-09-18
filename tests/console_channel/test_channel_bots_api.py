import re
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BotAccount

from console_channel.conftest import ChannelContext

BOTS_URL = "/internal/channel/bots"
REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ITEM_KEYS = {"bot_account_id", "bot_id", "secret", "agent_id", "enabled"}


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
