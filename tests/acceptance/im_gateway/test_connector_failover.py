"""[E-03][E-04][E-07] Connector 崩溃窗口、reply_ref 降级与显式 LOST。

真实边界：
- 真实 `muad_im_connector.main` 子进程 + 真实 Redis Stream/lease + 真实本地 WSS 探针；
- E-03/E-07 用 `CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC` 在**写 RECEIVED 之后、XADD 之前**
  暂停，再 SIGKILL，确定性复现"入站持久化前崩溃"；不是竞态碰运气；
- E-03 的平台重投由 WS 探针再次推送同一条 message_id，随后让真实 Runtime 以同一
  `Idempotency-Key` 受理两次，断言最终只有一个 Run；
- E-07 不重投：重启扫描必须把标记置 LOST、记指标/ERROR，且不补造 Run；
- E-04 的命令都进真实 `im.outbound:{tenant}:{bot}` Stream，结果由真实 OutboundRunner 回写。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import redis.asyncio as redis
from redis.asyncio import Redis

from tests.acceptance.im_gateway.connector_environment import (
    BOT_ID,
    INTERNAL_TOKEN,
    TENANT,
    ConnectorStack,
    kill_process,
    restart_process,
    start_connector_stack,
    stop_connector_stack,
)
from tests.e2e.wecom_probe_app import WeComProbe

pytestmark = pytest.mark.integration

LEASE_KEY = f"connector:bot:{TENANT}:{BOT_ID}"
OUTBOUND_KEY = f"im.outbound:{TENANT}:{BOT_ID}"


@pytest.fixture(scope="module")
def failover_probe() -> Iterator[WeComProbe]:
    yield WeComProbe(expected_bots={BOT_ID: "e2e-im-bot-secret"})


@pytest.fixture(scope="module")
async def failover_stack(
    tmp_path_factory: pytest.TempPathFactory, failover_probe: WeComProbe
) -> AsyncIterator[ConnectorStack]:
    root = tmp_path_factory.mktemp("connector-failover")
    await failover_probe.start()
    stack, processes = start_connector_stack(
        root,
        ws_probe_url=failover_probe.ws_url,
        ws_ca_file=str(failover_probe.cert_path),
        connectors=1,
    )
    stack.ws_probe = failover_probe
    try:
        yield stack
    finally:
        stop_connector_stack(processes)
        await failover_probe.stop()
        from tests.acceptance.im_gateway.environment import purge_tenant

        await purge_tenant()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    url = os.environ.get("REDIS_URL")
    assert url
    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


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


async def _lease(redis_client: Redis) -> dict[str, Any] | None:
    raw = await redis_client.get(LEASE_KEY)
    return json.loads(raw) if raw else None


async def _wait_connected(
    stack: ConnectorStack, probe: WeComProbe, *, at_least: int = 1
) -> None:
    await _wait_for(
        lambda: _has_subscribes(probe, at_least),
        what=f"Connector 未建立 WS 连接（期望至少 {at_least} 次 SUBSCRIBE）",
        timeout=30.0,
    )
    await _wait_for(lambda: _lease_ready(stack, probe), what="lease 未就绪", timeout=20.0)


def _subscribe_count(probe: WeComProbe) -> int:
    return len(
        [
            frame
            for frame in probe.frames_of("aibot_subscribe")
            if str((frame.get("body") or {}).get("bot_id") or "") == BOT_ID
        ]
    )


async def _has_subscribes(probe: WeComProbe, count: int) -> bool:
    return _subscribe_count(probe) >= count


async def _lease_ready(stack: ConnectorStack, probe: WeComProbe) -> bool:
    import redis.asyncio as redis_mod

    client = redis_mod.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        return await client.exists(LEASE_KEY) == 1 and _subscribe_count(probe) > 0
    finally:
        await client.aclose()





async def _inbound_entries(redis_client: Redis, message_id: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for key in await redis_client.keys("im.inbound:*"):
        for _entry_id, fields in await redis_client.xrange(key):
            if fields.get("external_message_id") == message_id:
                found.append(fields)
    return found


async def _publish_command(
    redis_client: Redis, *, kind: str, text: str, reply_ref: str | None, epoch: int
) -> tuple[uuid.UUID, str]:
    from muad_contracts.im import DeliveryCommand, DeliveryKind

    command = DeliveryCommand(
        command_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT_ID,
        run_id=uuid.uuid4(),
        external_user_id="e2e-im-ext-bound",
        external_conversation_id="e2e-im-chat",
        kind=DeliveryKind(kind),
        text=text,
        reply_ref=reply_ref,
        epoch=epoch,
        created_at=datetime.now(UTC),
    )
    await redis_client.xadd(
        OUTBOUND_KEY, {"event": command.model_dump_json()}, maxlen=10_000, approximate=True
    )
    return command.command_id, f"im:outbound:result:{command.command_id}"


async def _result(redis_client: Redis, key: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = await redis_client.get(key)
        if raw:
            return json.loads(raw)
        await asyncio.sleep(0.05)
    raise AssertionError(f"outbound result missing: {key}")


def _restart_with(process: Any, **env: str) -> None:
    process.stop()
    process.env.update(env)
    restart_process(process)


async def _runs_after(stack: ConnectorStack, started_at: datetime) -> int:
    from sqlalchemy import text

    from tests.acceptance.im_gateway.environment import _with_own_engine

    async def count(session_factory: Any) -> int:
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT count(*) FROM runtime.run_record "
                    "WHERE tenant_id = :t AND create_time >= :started"
                ),
                {"t": TENANT, "started": started_at},
            )
            return int(result.scalar() or 0)

    return int(await _with_own_engine(count))


async def test_e04_expired_reply_ref_degrades_to_active_without_wrong_callback(
    failover_stack: ConnectorStack,
    failover_probe: WeComProbe,
    redis_client: Redis,
) -> None:
    connector = failover_stack.connector(0)
    before = _subscribe_count(failover_probe)
    _restart_with(
        connector,
        CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC="0",
        CONNECTOR_INBOUND_LOST_AFTER_SEC="60",
    )
    await _wait_connected(failover_stack, failover_probe, at_least=before + 1)
    lease = await _lease(redis_client)
    assert lease is not None
    epoch = int(lease["epoch"])

    message_id = f"e04-{uuid.uuid4().hex[:8]}"
    reply_ref = f"req-{message_id}"
    await failover_probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="e2e-im-ext-bound",
        text="E-04 入站",
        reply_id=reply_ref,
        chat_id="e2e-im-chat",
    )
    await _wait_for(
        lambda: _inbound_entries(redis_client, message_id),
        what="E-04 入站未发布",
        timeout=10.0,
    )
    stored = await redis_client.hgetall(
        f"im:reply:message:{TENANT}:{BOT_ID}:{message_id}"
    )
    assert stored.get("reply_ref") == reply_ref

    # ① 有效 reply_ref：会话内回复，带原 req_id。
    command_id, result_key = await _publish_command(
        redis_client, kind="TEXT", text="会话内回复", reply_ref=reply_ref, epoch=epoch
    )
    result = await _result(redis_client, result_key)
    assert result["status"] == "DELIVERED", result
    await _wait_for(
        lambda: _has_reply(failover_probe, reply_ref),
        what="未观察到会话内回复帧",
        timeout=5.0,
    )

    # ② 删除 reply 目标（模拟 5 分钟 TTL 过期）：必须主动发送 + DEGRADED，且不得冒用旧 req_id。
    await redis_client.delete(
        f"im:reply:message:{TENANT}:{BOT_ID}:{message_id}",
        f"im:reply:ref:{TENANT}:{BOT_ID}:{reply_ref}",
    )
    expired_ref = f"req-expired-{uuid.uuid4().hex[:8]}"
    _command_id2, result_key2 = await _publish_command(
        redis_client, kind="TEXT", text="过期后主动发送", reply_ref=expired_ref, epoch=epoch
    )
    result2 = await _result(redis_client, result_key2)
    assert result2["status"] == "DEGRADED", result2
    assert result2["code"] == "REPLY_REF_EXPIRED"
    await _wait_for(
        lambda: _has_active_text(failover_probe, "过期后主动发送"),
        what="过期回调未走主动发送",
        timeout=5.0,
    )
    assert not any(
        ((frame.get("headers") or {}).get("req_id") == expired_ref)
        for frame in failover_probe.frames_of("aibot_respond_msg")
    ), "过期回调不得被冒用；必须发给会话而不是挂到别的消息上"


async def test_e03_crash_before_xadd_then_platform_redelivery_creates_one_run(
    failover_stack: ConnectorStack,
    failover_probe: WeComProbe,
    redis_client: Redis,
) -> None:
    connector = failover_stack.connector(0)
    before = _subscribe_count(failover_probe)
    _restart_with(
        connector,
        CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC="30",
        CONNECTOR_INBOUND_LOST_AFTER_SEC="60",
    )
    await _wait_connected(failover_stack, failover_probe, at_least=before + 1)

    message_id = f"e03-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(UTC)
    await failover_probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="e2e-im-ext-bound",
        text="E-03 崩溃前",
        reply_id=f"req-{message_id}",
        chat_id="e2e-im-chat",
    )
    dedupe_key = f"connector:inbound:{TENANT}:{BOT_ID}:{message_id}"
    await _wait_for(
        lambda: _is_received(redis_client, dedupe_key),
        what="崩溃点未到达（RECEIVED 标记未写入）",
        timeout=10.0,
    )
    kill_process(connector)
    assert not await _inbound_entries(redis_client, message_id), "崩溃点必须在 XADD 之前"

    # 重启（同一 owner 立即重获 lease），平台重投同一条 message_id。
    before_redelivery = _subscribe_count(failover_probe)
    _restart_with(
        connector,
        CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC="0",
        CONNECTOR_INBOUND_LOST_AFTER_SEC="60",
    )
    await _wait_connected(
        failover_stack, failover_probe, at_least=before_redelivery + 1
    )
    await failover_probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="e2e-im-ext-bound",
        text="E-03 崩溃前",
        reply_id=f"req-{message_id}",
        chat_id="e2e-im-chat",
    )
    await _wait_for(
        lambda: _inbound_entries(redis_client, message_id),
        what="重投后仍未入 Stream",
        timeout=10.0,
    )
    entries = await _inbound_entries(redis_client, message_id)
    assert len(entries) >= 1
    event = json.loads(entries[0]["event"])

    # 两次消费同一条入站事实（Stream 至少一次）：Runtime 按 message_id 幂等，只能建一个 Run。
    for _ in range(2):
        await _submit_inbound(failover_stack, event)
    await asyncio.sleep(1.0)
    assert await _runs_after(failover_stack, started_at) == 1, "平台重投不得建出第二个 Run"


async def test_e07_no_redelivery_is_explicitly_lost_and_never_fakes_a_run(
    failover_stack: ConnectorStack,
    failover_probe: WeComProbe,
    redis_client: Redis,
) -> None:
    connector = failover_stack.connector(0)
    before = _subscribe_count(failover_probe)
    _restart_with(
        connector,
        CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC="30",
        CONNECTOR_INBOUND_LOST_AFTER_SEC="1",
    )
    await _wait_connected(failover_stack, failover_probe, at_least=before + 1)

    message_id = f"e07-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(UTC)
    await failover_probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="e2e-im-ext-bound",
        text="E-07 不重投",
        reply_id=f"req-{message_id}",
        chat_id="e2e-im-chat",
    )
    dedupe_key = f"connector:inbound:{TENANT}:{BOT_ID}:{message_id}"
    await _wait_for(
        lambda: _is_received(redis_client, dedupe_key),
        what="崩溃点未到达",
        timeout=10.0,
    )
    kill_process(connector)
    await asyncio.sleep(1.2)

    before_restart = _subscribe_count(failover_probe)
    _restart_with(
        connector,
        CONNECTOR_TEST_PAUSE_BEFORE_XADD_SEC="0",
        CONNECTOR_INBOUND_LOST_AFTER_SEC="1",
    )
    await _wait_connected(
        failover_stack, failover_probe, at_least=before_restart + 1
    )
    await _wait_for(
        lambda: _is_lost(redis_client, dedupe_key),
        what="超时未重投的入站标记未被判 LOST",
        timeout=10.0,
    )
    assert not await _inbound_entries(redis_client, message_id)
    assert await _runs_after(failover_stack, started_at) == 0, "不重投时不得补造 Run"
    assert "connector_inbound_lost" in connector.log_path.read_text(errors="replace")
    metrics = httpx.get(f"{connector.url}/metrics", timeout=5.0).text
    assert "connector_inbound_lost_total" in metrics


async def _is_received(redis_client: Redis, key: str) -> bool:
    value = await redis_client.get(key)
    return bool(value and value.startswith("RECEIVED"))


async def _is_lost(redis_client: Redis, key: str) -> bool:
    value = await redis_client.get(key)
    return bool(value and value.startswith("LOST"))


def _has_reply(probe: WeComProbe, reply_ref: str) -> bool:
    return any(
        (frame.get("headers") or {}).get("req_id") == reply_ref
        for frame in probe.frames_of("aibot_respond_msg")
    )


def _has_active_text(probe: WeComProbe, text: str) -> bool:
    from tests.e2e.wecom_probe_app import frame_text

    return any(
        frame_text(frame) == text for frame in probe.frames_of("aibot_send_msg")
    )


async def _submit_inbound(stack: ConnectorStack, event: dict[str, Any]) -> None:
    payload = {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "conversation_id": None,
        "channel": {
            "type": "WECOM",
            "bot_id": event["bot_id"],
            "external_user_id": event["external_user_id"],
            "external_conversation_id": event.get("external_conversation_id"),
        },
        "message": {
            "id": event["external_message_id"],
            "type": "text",
            "text": event.get("text") or "redelivered",
        },
    }
    headers = {
        "X-Tenant-Id": TENANT,
        "X-Internal-Service": INTERNAL_TOKEN,
        "Idempotency-Key": event["external_message_id"],
    }
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        response = await client.post(
            f"{stack.runtime_url}/v1/runs", json=payload, headers=headers
        )
    assert response.status_code in (200, 202), response.text
