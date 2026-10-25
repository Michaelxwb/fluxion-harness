"""[S-05] 同一 bot 只有一个 Connector owner；SIGKILL 后接管且不重复入站。

真实边界（不 mock 业务服务）：
- 两个真实 `muad_im_connector.main` uvicorn 子进程，真实 Console 快照、真实 Redis lease；
- 真实本地 WSS 探针承载官方企微 SDK（不是 SDK 替身）；
- SIGKILL owner（不触发优雅释放），新 owner 只能等租约过期后接管；
- 入站消息经真实 WS 回调 → 真实 Connector → 真实 Redis Stream。

断言逐项对应 S-05：任意时刻只有一个 active owner；旧 owner epoch 被拒绝；接管后新消息可处理；
无重复入站。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import redis.asyncio as redis
from redis.asyncio import Redis

from tests.acceptance.im_gateway.connector_environment import (
    BOT_ID,
    ConnectorStack,
    kill_process,
    start_connector_stack,
    stop_connector_stack,
)
from tests.e2e.wecom_probe_app import CMD_SUBSCRIBE, WeComProbe

pytestmark = pytest.mark.integration

LEASE_KEY = f"connector:bot:e2e-im-gateway:{BOT_ID}"


@pytest.fixture(scope="module")
def connector_probe() -> Iterator[WeComProbe]:
    probe = WeComProbe(expected_bots={BOT_ID: "e2e-im-bot-secret"})
    yield probe


@pytest.fixture(scope="module")
async def connector_stack(
    tmp_path_factory: pytest.TempPathFactory, connector_probe: WeComProbe
) -> AsyncIterator[ConnectorStack]:
    root = tmp_path_factory.mktemp("connector-lease")
    await connector_probe.start()
    stack, processes = start_connector_stack(
        root,
        ws_probe_url=connector_probe.ws_url,
        ws_ca_file=str(connector_probe.cert_path),
        connectors=2,
    )
    stack.ws_probe = connector_probe
    try:
        yield stack
    finally:
        stop_connector_stack(processes)
        await connector_probe.stop()
        from tests.acceptance.im_gateway.environment import purge_tenant

        await purge_tenant()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    url = os.environ.get("REDIS_URL")
    assert url, "REDIS_URL 必须存在"
    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def _subscribes_for_bot(probe: WeComProbe) -> list[dict[str, object]]:
    return [
        frame
        for frame in probe.frames_of(CMD_SUBSCRIBE)
        if str((frame.get("body") or {}).get("bot_id") or "") == BOT_ID
    ]


def _lease_owner(raw: str | None) -> tuple[str, int] | None:
    if not raw:
        return None
    payload = json.loads(raw)
    return str(payload.get("owner_id") or ""), int(payload.get("epoch") or 0)


async def _wait_for(predicate: object, *, what: str, timeout: float = 20.0) -> None:
    import inspect

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()  # type: ignore[operator]
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(what)


async def test_s05_single_owner_takeover_after_sigkill_and_no_duplicate_inbound(
    connector_stack: ConnectorStack,
    connector_probe: WeComProbe,
    redis_client: Redis,
) -> None:
    # ① 两个 Connector 都起来后，只有一个 owner、只有一条 SUBSCRIBE。
    await _wait_for(
        lambda: _has_subscriptions(connector_probe),
        what="owner Connector 未建立 WS 连接",
        timeout=30.0,
    )
    await asyncio.sleep(2.5)  # 让 loser 完成至少两拍快照/租约尝试
    raw = await redis_client.get(LEASE_KEY)
    current = _lease_owner(raw)
    assert current is not None, "lease 必须已写入"
    owner, epoch = current
    assert owner in {"connector-0", "connector-1"}
    subscribes = _subscribes_for_bot(connector_probe)
    assert len(subscribes) == 1, (
        f"任意时刻只允许一个 active owner；实际 SUBSCRIBE {len(subscribes)} 次"
    )

    # ② SIGKILL owner：不做优雅释放，必须等 TTL 过期后由另一进程接管。
    owner_process = connector_stack.owner_process(owner)
    kill_process(owner_process)
    survivor = "connector-1" if owner == "connector-0" else "connector-0"

    await _wait_for(
        lambda: _current_owner(redis_client),
        what="接管后 lease 未写入",
        timeout=20.0,
    )
    await _wait_for(
        lambda: _owned_by(redis_client, survivor),
        what=f"{survivor} 未在租约过期后接管",
        timeout=25.0,
    )
    raw_after = await redis_client.get(LEASE_KEY)
    after = _lease_owner(raw_after)
    assert after is not None
    assert after[1] > epoch, "接管必须使用更高 epoch（旧 owner 被 fencing）"
    await _wait_for(
        lambda: _has_at_least_subscriptions(connector_probe, 2),
        what="新 owner 未建立 WS 连接",
        timeout=30.0,
    )

    # ③ 接管后新消息只产生一条入站事实（无重复）。
    message_id = "s05-after-takeover-1"
    await connector_probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="e2e-im-ext-bound",
        text="接管后你好",
        reply_id="s05-req-1",
        chat_id="e2e-im-chat",
    )
    await _wait_for(
        lambda: _inbound_entries(redis_client, message_id),
        what="接管后入站未进入 Stream",
        timeout=10.0,
    )
    entries = await _inbound_entries(redis_client, message_id)
    assert len(entries) == 1, f"同一条消息不得重复入站：{entries}"
    event = json.loads(entries[0]["event"])
    assert event["bot_id"] == BOT_ID
    assert event["reply_ref"] == "s05-req-1"
    assert event["external_message_id"] == message_id

    stored = await redis_client.hgetall(
        f"im:reply:message:e2e-im-gateway:{BOT_ID}:{message_id}"
    )
    assert stored.get("reply_ref") == "s05-req-1", "接管后的 reply store 必须可跨进程读取"


async def _has_subscriptions(probe: WeComProbe) -> bool:
    return bool(_subscribes_for_bot(probe))


async def _has_at_least_subscriptions(probe: WeComProbe, count: int) -> bool:
    return len(_subscribes_for_bot(probe)) >= count


async def _current_owner(redis: Redis) -> tuple[str, int] | None:
    return _lease_owner(await redis.get(LEASE_KEY))


async def _owned_by(redis: Redis, owner: str) -> bool:
    current = await _current_owner(redis)
    return current is not None and current[0] == owner


async def _inbound_entries(redis: Redis, message_id: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for key in await redis.keys("im.inbound:*"):
        for _entry_id, fields in await redis.xrange(key):
            if fields.get("external_message_id") == message_id:
                found.append(fields)
    return found
