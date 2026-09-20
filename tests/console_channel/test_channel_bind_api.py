import uuid

from httpx import AsyncClient, Response
from muad_console_platform.application.channel_service import hash_bind_code
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BindCode, ChannelIdentity
from sqlalchemy import func, select

from console_channel.conftest import CHANNEL, ChannelContext

BIND_URL = "/internal/channel/bind"


def _headers(channel: ChannelContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or channel.tenant_id}


def _payload(bot_id: str, external_user_id: str, bind_code: str) -> dict[str, str]:
    return {
        "channel": CHANNEL,
        "bot_id": bot_id,
        "external_user_id": external_user_id,
        "bind_code": bind_code,
    }


def _assert_error(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["code"] == code


async def _fetch_code(channel: ChannelContext, plain_code: str) -> BindCode | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            select(BindCode).where(
                BindCode.tenant_id == channel.tenant_id,
                BindCode.code_hash == hash_bind_code(plain_code),
            )
        )


async def _count_identities(channel: ChannelContext, external_user_id: str) -> int:
    async with get_session_factory()() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(ChannelIdentity)
            .where(
                ChannelIdentity.tenant_id == channel.tenant_id,
                ChannelIdentity.external_user_id == external_user_id,
            )
        )
        return int(total or 0)


async def _fetch_identity(channel: ChannelContext, external_user_id: str) -> ChannelIdentity | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.tenant_id == channel.tenant_id,
                ChannelIdentity.external_user_id == external_user_id,
            )
        )


async def test_bind_consumes_code_and_creates_identity(
    client: AsyncClient, channel: ChannelContext
) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.unbound_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"] == {
        "platform_user_id": str(channel.actor_user_id),
        "bound": True,
    }
    identity = await _fetch_identity(channel, channel.unbound_external_user_id)
    assert identity is not None
    assert identity.platform_user_id == channel.actor_user_id
    assert identity.bot_account_id is not None
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "USED"
    assert bind_code.used_at is not None
    assert bind_code.used_channel_identity_id == identity.id


async def test_reusing_consumed_code_is_invalid(
    client: AsyncClient, channel: ChannelContext
) -> None:
    payload = _payload(
        channel.bot_id,
        channel.unbound_external_user_id,
        channel.valid_bind_code,
    )
    first = await client.post(BIND_URL, json=payload, headers=_headers(channel))
    assert first.status_code == 200
    second = await client.post(BIND_URL, json=payload, headers=_headers(channel))
    _assert_error(second, 400, "BIND_CODE_INVALID")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 1


async def test_expired_code_is_expired(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.unbound_external_user_id,
            channel.expired_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 410, "BIND_CODE_EXPIRED")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0


async def test_used_code_is_invalid(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.unbound_external_user_id,
            channel.used_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 400, "BIND_CODE_INVALID")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0


async def test_unknown_code_is_invalid(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.unbound_external_user_id,
            channel.unknown_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 400, "BIND_CODE_INVALID")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0


async def test_existing_identity_returns_existing_user(
    client: AsyncClient, channel: ChannelContext
) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.bound_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"] == {
        "platform_user_id": str(channel.actor_user_id),
        "bound": True,
    }
    assert await _count_identities(channel, channel.bound_external_user_id) == 1
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "USED"


async def test_identity_bound_to_another_user_is_rejected(
    client: AsyncClient, channel: ChannelContext
) -> None:
    # ungranted_external_user_id 已绑定 ungranted_user_id，而 valid_bind_code 的目标是 actor_user_id
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.ungranted_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 409, "IDENTITY_ALREADY_BOUND")

    identity = await _fetch_identity(channel, channel.ungranted_external_user_id)
    assert identity is not None
    assert identity.platform_user_id == channel.ungranted_user_id

    # 码不被消费：解绑后可复用
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "ACTIVE"
    assert bind_code.used_at is None
    assert bind_code.used_channel_identity_id is None


async def test_other_tenant_code_is_invalid(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.bot_id,
            channel.unbound_external_user_id,
            channel.other_tenant_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 400, "BIND_CODE_INVALID")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0


async def test_disabled_bot_is_not_found(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.disabled_bot_id,
            channel.unbound_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 404, "BOT_NOT_FOUND")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "ACTIVE"


async def test_unknown_bot_is_not_found(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            f"unknown-{uuid.uuid4()}",
            channel.unbound_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 404, "BOT_NOT_FOUND")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "ACTIVE"


async def test_deleted_bot_is_not_found(client: AsyncClient, channel: ChannelContext) -> None:
    response = await client.post(
        BIND_URL,
        json=_payload(
            channel.deleted_bot_id,
            channel.unbound_external_user_id,
            channel.valid_bind_code,
        ),
        headers=_headers(channel),
    )
    _assert_error(response, 404, "BOT_NOT_FOUND")
    assert await _count_identities(channel, channel.unbound_external_user_id) == 0
    bind_code = await _fetch_code(channel, channel.valid_bind_code)
    assert bind_code is not None
    assert bind_code.status == "ACTIVE"


async def test_blank_external_user_returns_validation_envelope(
    client: AsyncClient, channel: ChannelContext
) -> None:
    response = await client.post(
        BIND_URL,
        json={
            "channel": CHANNEL,
            "bot_id": channel.bot_id,
            "external_user_id": "",
            "bind_code": channel.valid_bind_code,
        },
        headers=_headers(channel),
    )
    _assert_error(response, 422, "COMMON_VALIDATION_ERROR")
