"""B-103: Console bind 持久幂等与事务重放（真实 bind HTTP handler → PostgreSQL）。

不得 Mock 的真实边界：真实 FastAPI handler + 真实 PostgreSQL（幂等记录、bind_code 行锁、
channel_identity），断言直接作用于数据库行与 HTTP 响应。
"""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient, Response
from muad_console_platform.application.channel_service import hash_bind_code
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BindCode, ChannelIdentity
from muad_console_platform.infrastructure.models.control import SkillImportIdempotency
from sqlalchemy import func, select

from console_channel.conftest import CHANNEL, ChannelContext

BIND_URL = "/internal/channel/bind"
BIND_ENDPOINT = "/internal/channel/bind"
DECOY_ENDPOINT = "/internal/channel/other"


def _headers(channel: ChannelContext, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Tenant-Id": channel.tenant_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _payload(bot_id: str, external_user_id: str, bind_code: str) -> dict[str, str]:
    return {
        "channel": CHANNEL,
        "bot_id": bot_id,
        "external_user_id": external_user_id,
        "bind_code": bind_code,
    }


async def _post_bind(
    client: AsyncClient,
    channel: ChannelContext,
    *,
    bot_id: str,
    external_user_id: str,
    bind_code: str,
    idempotency_key: str | None,
    tenant_id: str | None = None,
) -> Response:
    headers = _headers(channel, idempotency_key)
    if tenant_id is not None:
        headers["X-Tenant-Id"] = tenant_id
    return await client.post(
        BIND_URL,
        json=_payload(bot_id, external_user_id, bind_code),
        headers=headers,
    )


async def _fetch_idempotency(
    channel: ChannelContext,
    idempotency_key: str,
    endpoint: str = BIND_ENDPOINT,
) -> SkillImportIdempotency | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == channel.tenant_id,
                SkillImportIdempotency.idempotency_key == idempotency_key,
                SkillImportIdempotency.endpoint == endpoint,
            )
        )


async def _count_identities(channel: ChannelContext, external_user_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ChannelIdentity)
                .where(
                    ChannelIdentity.tenant_id == channel.tenant_id,
                    ChannelIdentity.external_user_id == external_user_id,
                )
            )
            or 0
        )


async def _fetch_code(channel: ChannelContext, plain_code: str) -> BindCode | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            select(BindCode).where(
                BindCode.tenant_id == channel.tenant_id,
                BindCode.code_hash == hash_bind_code(plain_code),
            )
        )


async def test_b103_replay_returns_first_response_without_second_consumption(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    body = {
        "bot_id": channel.bot_id,
        "external_user_id": channel.unbound_external_user_id,
        "bind_code": channel.valid_bind_code,
    }
    first = await _post_bind(client, channel, idempotency_key=key, **body)
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data == {"platform_user_id": str(channel.actor_user_id), "bound": True}

    # 重放：新会话（重启后等价）同 key 同指纹 → 返回首次响应，不再消费
    replay = await _post_bind(client, channel, idempotency_key=key, **body)
    assert replay.status_code == 200
    assert replay.json()["data"] == first_data
    assert await _count_identities(channel, channel.unbound_external_user_id) == 1

    record = await _fetch_idempotency(channel, key)
    assert record is not None
    assert record.response_json == first_data
    assert channel.valid_bind_code not in str(record.request_fingerprint)

    # 无同 key 重放时，已用绑定码仍按单次规则拒绝
    other_key = f"msg-{uuid.uuid4()}"
    reused = await _post_bind(client, channel, idempotency_key=other_key, **body)
    assert reused.status_code == 404 or reused.status_code == 400
    assert reused.json()["code"] == "BIND_CODE_INVALID"


async def test_b103_same_key_different_fingerprint_conflicts(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    first = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert first.status_code == 200

    other_user = f"ext-other-{uuid.uuid4()}"
    mismatch = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=other_user,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await _count_identities(channel, other_user) == 0


async def test_b103_concurrent_same_key_consumes_once(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    body = {
        "bot_id": channel.bot_id,
        "external_user_id": channel.unbound_external_user_id,
        "bind_code": channel.valid_bind_code,
    }
    responses = await asyncio.gather(
        *(_post_bind(client, channel, idempotency_key=key, **body) for _ in range(2))
    )
    assert [response.status_code for response in responses] == [200, 200]
    payloads = [response.json()["data"] for response in responses]
    assert payloads[0] == payloads[1]
    assert await _count_identities(channel, channel.unbound_external_user_id) == 1
    code = await _fetch_code(channel, channel.valid_bind_code)
    assert code is not None and code.status == "USED"


async def test_b103_failed_attempt_rolls_back_and_retry_with_same_key_succeeds(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    invalid = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.unknown_bind_code,
        idempotency_key=key,
    )
    assert invalid.json()["code"] == "BIND_CODE_INVALID"
    assert await _fetch_idempotency(channel, key) is None  # 失败事务不留下成功响应

    retried = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert retried.status_code == 200
    assert await _fetch_idempotency(channel, key) is not None


async def test_b103_tenant_and_endpoint_are_isolated(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        # 同一 key 挂在其他 endpoint 上，不得被 bind 重放
        session.add(
            SkillImportIdempotency(
                tenant_id=channel.tenant_id,
                idempotency_key=key,
                endpoint=DECOY_ENDPOINT,
                request_fingerprint="sha256:" + "0" * 64,
                response_json={"platform_user_id": "decoy", "bound": True},
            )
        )
        await session.commit()

    first = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert first.status_code == 200
    assert first.json()["data"]["platform_user_id"] == str(channel.actor_user_id)

    # 另一个租户用同一个 key 独立处理，不影响本租户重放结果
    other = await _post_bind(
        client,
        channel,
        bot_id=channel.other_bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.other_tenant_bind_code,
        idempotency_key=key,
        tenant_id=channel.other_tenant_id,
    )
    assert other.status_code == 200

    replay = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert replay.json()["data"] == first.json()["data"]
    assert await _fetch_idempotency(channel, key) is not None
    assert await _fetch_idempotency(channel, key, DECOY_ENDPOINT) is not None


async def test_b103_storage_keeps_checksums_not_plaintext(
    client: AsyncClient, channel: ChannelContext
) -> None:
    key = f"msg-{uuid.uuid4()}"
    response = await _post_bind(
        client,
        channel,
        bot_id=channel.bot_id,
        external_user_id=channel.unbound_external_user_id,
        bind_code=channel.valid_bind_code,
        idempotency_key=key,
    )
    assert response.status_code == 200

    code = await _fetch_code(channel, channel.valid_bind_code)
    assert code is not None
    assert code.code_hash == hash_bind_code(channel.valid_bind_code)
    assert channel.valid_bind_code not in code.code_hash

    record = await _fetch_idempotency(channel, key)
    assert record is not None
    fingerprint_and_response = f"{record.request_fingerprint}{record.response_json}"
    assert channel.valid_bind_code not in fingerprint_and_response
    assert response.json()["data"] == record.response_json
