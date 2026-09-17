import uuid
from typing import Any

from httpx import AsyncClient

from console_channel.conftest import CHANNEL, ChannelContext

RESOLVE_URL = "/internal/channel/resolve"


def _headers(channel: ChannelContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or channel.tenant_id}


def _payload(bot_id: str, external_user_id: str, channel_name: str = CHANNEL) -> dict[str, str]:
    return {
        "channel": channel_name,
        "bot_id": bot_id,
        "external_user_id": external_user_id,
    }


async def _resolve(
    client: AsyncClient,
    channel: ChannelContext,
    *,
    bot_id: str,
    external_user_id: str,
    tenant_id: str | None = None,
) -> dict[str, object]:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(bot_id, external_user_id),
        headers=_headers(channel, tenant_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    data: dict[str, Any] = body["data"]
    return data


async def test_unknown_bot_returns_unbound(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=f"unknown-{uuid.uuid4()}",
        external_user_id=channel.unbound_external_user_id,
    )
    assert data == {
        "bound": False,
        "agent_id": None,
        "platform_user_id": None,
        "authorized": False,
    }


async def test_disabled_bot_returns_unbound(client: AsyncClient, channel: ChannelContext) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.disabled_bot_id,
        external_user_id=channel.unbound_external_user_id,
    )
    assert data == {
        "bound": False,
        "agent_id": None,
        "platform_user_id": None,
        "authorized": False,
    }


async def test_deleted_bot_returns_unbound(client: AsyncClient, channel: ChannelContext) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.deleted_bot_id,
        external_user_id=channel.unbound_external_user_id,
    )
    assert data == {
        "bound": False,
        "agent_id": None,
        "platform_user_id": None,
        "authorized": False,
    }


async def test_known_bot_without_identity_returns_agent(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
    )
    assert data == {
        "bound": False,
        "agent_id": str(channel.agent_id),
        "platform_user_id": None,
        "authorized": False,
    }


async def test_bound_without_grant_is_unauthorized(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.ungranted_external_user_id,
    )
    assert data == {
        "bound": True,
        "agent_id": str(channel.agent_id),
        "platform_user_id": str(channel.ungranted_user_id),
        "authorized": False,
    }


async def test_bound_with_grant_is_authorized(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.bound_external_user_id,
    )
    assert data == {
        "bound": True,
        "agent_id": str(channel.agent_id),
        "platform_user_id": str(channel.actor_user_id),
        "authorized": True,
    }


async def test_disabled_platform_user_is_unauthorized(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.disabled_external_user_id,
    )
    assert data == {
        "bound": True,
        "agent_id": str(channel.agent_id),
        "platform_user_id": str(channel.disabled_user_id),
        "authorized": False,
    }


async def test_tenant_isolation_hides_other_tenant_bot(
    client: AsyncClient, channel: ChannelContext
) -> None:
    data = await _resolve(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.bound_external_user_id,
        tenant_id=channel.other_tenant_id,
    )
    assert data == {
        "bound": False,
        "agent_id": None,
        "platform_user_id": None,
        "authorized": False,
    }
