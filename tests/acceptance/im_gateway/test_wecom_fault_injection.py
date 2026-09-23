"""B-131: WS/SDK 故障注入边界（生产 WeComAdapter → 真实本地 WS 服务 → 三类注入）。

不得 Mock 的真实边界：生产 `WeComAdapter` 经生产 SDK 工厂（`WECOM_WS_URL` /
`WECOM_WS_CA_FILE` seam）连到真实 `wss://` 探针；注入由探针在真实 socket 上实施，
断言 Adapter 真实观测到握手拒绝 / 断线 / 发送失败，且只影响目标 bot、清除后可恢复。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from muad_contracts import BotSnapshotItem, DeliveryMessage, DeliveryRouteInput
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter

from tests.e2e.wecom_probe_app import WeComProbe

GOOD_BOT = "bot-fault-good"
BAD_BOT = "bot-fault-bad"
SECRET = "fault-secret"
EXTERNAL_USER_ID = "ext-fault"
CHAT_ID = "chat-fault"
CONNECT_TIMEOUT_SEC = 25.0


def _bot(bot_id: str) -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid.uuid4(), bot_id=bot_id, secret=SECRET, agent_id=uuid.uuid4()
    )


async def _wait_state(adapter: WeComAdapter, bot_id: str, state: ConnectionState, timeout: float = CONNECT_TIMEOUT_SEC) -> ConnectionState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = adapter.connection_states.get(bot_id)
        if current is state:
            return current
        await asyncio.sleep(0.05)
    return adapter.connection_states.get(bot_id)  # type: ignore[return-value]


@pytest.fixture()
async def probe(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WeComProbe]:
    server = WeComProbe(expected_bots={GOOD_BOT: SECRET, BAD_BOT: SECRET})
    await server.start()
    monkeypatch.setenv("WECOM_WS_URL", server.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(server.cert_path))
    try:
        yield server
    finally:
        await server.stop()


@contextlib.asynccontextmanager
async def _adapter(bots: list[str]) -> AsyncIterator[WeComAdapter]:
    instance = WeComAdapter(
        bots=[_bot(bot_id) for bot_id in bots], backoff_base_sec=0.1, backoff_max_sec=0.5
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


async def test_b131_handshake_rejection_is_isolated_and_recovers(probe: WeComProbe) -> None:
    probe.reject_bot_ids.add(BAD_BOT)
    async with _adapter([GOOD_BOT, BAD_BOT]) as adapter:
        assert await _wait_state(adapter, GOOD_BOT, ConnectionState.CONNECTED) is ConnectionState.CONNECTED
        # 目标 bot 被拒绝：不 CONNECTED，且不影响另一个 bot
        await asyncio.sleep(0.5)
        assert adapter.connection_states[BAD_BOT] is not ConnectionState.CONNECTED
        assert adapter.connection_states[GOOD_BOT] is ConnectionState.CONNECTED
        assert adapter.last_error is not None

        # 清除注入后可恢复
        probe.clear_injections()
        assert await _wait_state(adapter, BAD_BOT, ConnectionState.CONNECTED) is ConnectionState.CONNECTED


async def test_b131_disconnect_is_observed_and_recovers(probe: WeComProbe) -> None:
    """断线注入：生产 Adapter 观测到服务端断线并退避重连（TASK-006 修复后生效）。"""
    async with _adapter([GOOD_BOT]) as adapter:
        assert await _wait_state(adapter, GOOD_BOT, ConnectionState.CONNECTED) is ConnectionState.CONNECTED
        initial_connections = len(probe.connections)

        await probe.drop_connection(GOOD_BOT)
        # 观测到断线（连接存活探测兜底，间隔有界）→ 退避 → 自行重连经真实 socket 重新握手
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and len(probe.connections) <= initial_connections:
            await asyncio.sleep(0.1)
        assert len(probe.connections) > initial_connections, "断线后应经真实 socket 重新握手"
        assert await _wait_state(adapter, GOOD_BOT, ConnectionState.CONNECTED) is ConnectionState.CONNECTED


async def test_b131_send_failure_is_observed_and_connection_survives(probe: WeComProbe) -> None:
    async with _adapter([GOOD_BOT]) as adapter:
        assert await _wait_state(adapter, GOOD_BOT, ConnectionState.CONNECTED) is ConnectionState.CONNECTED
        route = DeliveryRouteInput(
            channel="WECOM",
            bot_id=GOOD_BOT,
            external_user_id=EXTERNAL_USER_ID,
            external_conversation_id=CHAT_ID,
        )
        probe.fail_reply_bots.add(GOOD_BOT)
        with pytest.raises(Exception):  # noqa: B017 - SDK 以异常形式回传发送失败
            await adapter.send(route, DeliveryMessage(type="text", text="注入发送失败"))
        assert probe.replies, "发送尝试必须经真实 socket 到达探针"

        # 清除注入后连接仍可用
        probe.clear_injections()
        await adapter.send(route, DeliveryMessage(type="text", text="恢复后的发送"))
        await asyncio.sleep(0.3)
        assert adapter.connection_states[GOOD_BOT] is ConnectionState.CONNECTED
        assert probe.replies[-1]["body"]["text"]["content"] == "恢复后的发送"
