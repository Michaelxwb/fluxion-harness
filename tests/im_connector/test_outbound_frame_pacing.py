"""出站流式帧的合并节拍：状态帧与正文帧共享一个最小时距（企微限 30 条/分钟）。

为什么必须合并：每帧携带的是**累积全文**，窗口内少发几帧不会丢字；而超限会被企微服务端
限流成「攒着一次刷」，实测表现为「状态行一直在跳」「最后才蹦出一整段」。
终态/独立文本/文件是各自独立的事实，永远不被合并。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from muad_contracts import DeliveryRouteInput
from muad_contracts.im import DeliveryCommand, DeliveryKind
from muad_im_connector.outbound import CommandExecutor, CommandStatus

TENANT = "tenant-pacing"
BOT = "bot-pacing"
#: 正文缓冲按 Run 累积（`_buffer_key`）：同一段回复的多帧必须带同一个 run_id。
RUN = uuid.uuid4()


@dataclass(frozen=True)
class _Lease:
    owner_id: str
    epoch: int


class _Leases:
    def __init__(self, owner_id: str = "owner-1") -> None:
        self._owner_id = owner_id

    async def read(self, tenant_id: str, bot_id: str) -> _Lease:
        return _Lease(owner_id=self._owner_id, epoch=1)


@dataclass(frozen=True)
class _Target:
    reply_ref: str = "req-1"
    epoch: int = 1
    expires_at: float = 0.0


class _Replies:
    def __init__(self) -> None:
        self.streams: list[tuple[str, str]] = []
        self.finished: list[str] = []

    async def resolve_message(self, **_: object) -> _Target:
        return _Target()

    async def delivery_receipt(self, **_: object) -> None:
        return None

    async def is_finished(self, **_: object) -> bool:
        return False

    async def remember_stream(self, *, stream_id: str, last_text: str, **_: object) -> None:
        self.streams.append((stream_id, last_text))

    async def mark_finished(self, *, message_id: str, **_: object) -> None:
        self.finished.append(message_id)


class _Channel:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []
        self.frames: list[tuple[str, bool]] = []
        self.actives: list[str] = []

    def has_bot(self, bot_id: str) -> bool:
        return True

    def route_key(self, route: DeliveryRouteInput) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    async def send_status(
        self, route: DeliveryRouteInput, reply_ref: str, stream_id: str, status: str, body: str
    ) -> None:
        self.statuses.append((status, body))

    async def send_reply_stream(
        self, route: DeliveryRouteInput, reply_ref: str, stream_id: str, text: str, *, finish: bool
    ) -> None:
        self.frames.append((text, finish))

    async def send_active(self, route: DeliveryRouteInput, message: object) -> None:
        self.actives.append(getattr(message, "text", ""))


def _command(kind: DeliveryKind, *, message_id: str = "m-1", **overrides: object) -> DeliveryCommand:
    values: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "channel": "WECOM",
        "tenant_id": TENANT,
        "bot_id": BOT,
        "run_id": RUN,
        "message_id": message_id,
        "external_user_id": "user-1",
        "external_conversation_id": "chat-1",
        "kind": kind,
        "text": "hello",
        "stream_id": "stream-1",
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return DeliveryCommand(**values)  # type: ignore[arg-type]


def _executor(now: list[float], interval: float = 2.0) -> tuple[CommandExecutor, _Channel, _Replies]:
    channel, replies = _Channel(), _Replies()
    executor = CommandExecutor(
        adapter=channel,  # type: ignore[arg-type]
        leases=_Leases(),  # type: ignore[arg-type]
        replies=replies,  # type: ignore[arg-type]
        owner_id="owner-1",
        frame_min_interval_sec=interval,
        clock=lambda: now[0],
    )
    return executor, channel, replies


async def test_delta_within_window_is_merged_into_the_next_frame() -> None:
    now = [100.0]
    executor, channel, _ = _executor(now)

    first = await executor.execute(_command(DeliveryKind.DELTA, text="你"))
    now[0] += 0.5
    second = await executor.execute(_command(DeliveryKind.DELTA, text="好"))
    assert first.status is CommandStatus.DELIVERED
    assert second.status is CommandStatus.DELIVERED
    assert second.detail == "delta coalesced"
    assert channel.frames == [("你", False)], "窗口内的增量不发，等下一帧带全文"

    now[0] += 2.0
    await executor.execute(_command(DeliveryKind.DELTA, text="！"))
    assert channel.frames[-1] == ("你好！", False), "被合并的字必须出现在下一帧里，不能丢"


async def test_frame_at_the_nominal_cadence_is_never_swallowed_by_jitter() -> None:
    """节拍器与网关标称节拍同频（2s）：抖动 0.1s 以内的那一拍必须照发。

    回归：窗口等于节拍时，`now - last` 永远差一点点不到 2.0s，实测吞掉一半状态刷新
    （`00:00 → 00:04 → 00:08`）。
    """
    now = [100.0]
    executor, channel, _ = _executor(now)

    await executor.execute(_command(DeliveryKind.DELTA, payload={"status": True}, text="00:00"))
    now[0] += 1.9
    await executor.execute(_command(DeliveryKind.DELTA, payload={"status": True}, text="00:02"))
    assert [status for status, _ in channel.statuses] == ["00:00", "00:02"]


async def test_status_and_delta_share_one_budget() -> None:
    now = [100.0]
    executor, channel, _ = _executor(now)

    await executor.execute(
        _command(DeliveryKind.DELTA, payload={"status": True}, text="🔵 思考中 · 已执行 00:00")
    )
    now[0] += 1.0
    merged = await executor.execute(_command(DeliveryKind.DELTA, text="正文"))
    assert merged.detail == "delta coalesced"
    assert channel.statuses == [("🔵 思考中 · 已执行 00:00", "")]
    assert channel.frames == []


async def test_terminal_is_never_merged_and_carries_the_full_text() -> None:
    now = [100.0]
    executor, channel, replies = _executor(now)

    await executor.execute(_command(DeliveryKind.DELTA, text="你"))
    now[0] += 0.5
    await executor.execute(_command(DeliveryKind.DELTA, text="好"))
    # 持久投递链的终态带 delivery_key：正文是完整替换，不再叠加已流出的增量。
    terminal = await executor.execute(
        _command(DeliveryKind.TERMINAL, text="你好", delivery_key="run:x:terminal")
    )
    assert terminal.status is CommandStatus.DELIVERED
    assert channel.frames[-1] == ("你好", True), "终态永远照发，且是完整正文"
    assert replies.finished == ["m-1"]


async def test_different_messages_do_not_share_the_budget() -> None:
    now = [100.0]
    executor, channel, _ = _executor(now)

    await executor.execute(
        _command(DeliveryKind.DELTA, message_id="m-1", run_id=uuid.uuid4(), text="一")
    )
    await executor.execute(
        _command(DeliveryKind.DELTA, message_id="m-2", run_id=uuid.uuid4(), text="二")
    )
    assert [text for text, _ in channel.frames] == ["一", "二"]


async def test_pacing_can_be_disabled_for_other_deployments() -> None:
    now = [100.0]
    executor, channel, _ = _executor(now, interval=0.0)

    await executor.execute(_command(DeliveryKind.DELTA, text="一"))
    await executor.execute(_command(DeliveryKind.DELTA, text="二"))
    assert [text for text, _ in channel.frames] == ["一", "一二"]


async def test_priority_status_bypasses_the_shared_frame_budget() -> None:
    """阶段变化（准备中→思考中→执行中）是少量事实，不能被 2s 节拍吞掉。"""
    now = [100.0]
    executor, channel, _ = _executor(now)

    await executor.execute(
        _command(DeliveryKind.DELTA, payload={"status": True}, text="🔵 准备中")
    )
    now[0] += 0.5
    await executor.execute(
        _command(
            DeliveryKind.DELTA,
            payload={"status": True, "priority": True},
            text="🔵 思考中",
        )
    )
    assert [status for status, _ in channel.statuses] == ["🔵 准备中", "🔵 思考中"]


async def test_delivery_terminal_keeps_newer_buffer_text() -> None:
    """持久链终态若带旧正文，但缓冲区已含更新的确认结果，不能把新结果覆盖掉。"""
    now = [100.0]
    executor, channel, _ = _executor(now)

    await executor.execute(
        _command(DeliveryKind.DELTA, text="记忆已保存。请确认是否保存这条偏好。")
    )
    now[0] += 2.1
    await executor.execute(
        _command(
            DeliveryKind.TERMINAL,
            text="请确认是否保存这条偏好。",
            delivery_key="run:x:terminal",
        )
    )
    assert channel.frames[-1] == ("记忆已保存。请确认是否保存这条偏好。", True)
