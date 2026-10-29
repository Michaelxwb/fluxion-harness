"""通知 SSE 帧生成：先回填、再消费 live 信号、按 seq 去重。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from muad_im_gateway.api.webchat import _notification_frames
from muad_im_gateway.application.notification_streams import NotificationSignal


class _Item:
    def __init__(self, seq: int) -> None:
        self.seq = seq

    def model_dump(self, *, mode: str = "python") -> dict[str, int]:
        return {"seq": self.seq}


class _Page:
    def __init__(
        self, items: list[_Item], next_seq: int, through_seq: int, unread_count: int = 1
    ) -> None:
        self.items = items
        self.next_seq = next_seq
        self.through_seq = through_seq
        self.unread_count = unread_count


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def list_notifications(
        self, chat: object, *, after_seq: int, page: int, page_size: int
    ) -> _Page:
        self.calls.append((after_seq, page_size))
        if after_seq == 0:
            return _Page([_Item(1)], next_seq=1, through_seq=1)
        return _Page([_Item(2)], next_seq=2, through_seq=2)


class _Hub:
    def __init__(self, watermark: int | None = 1) -> None:
        self._watermark = watermark
        self.queue: asyncio.Queue[NotificationSignal | None] | None = None

    async def watermark(self, tenant_id: str, user_id: uuid.UUID) -> int | None:
        return self._watermark

    @asynccontextmanager
    async def subscribe(self, tenant_id: str, user_id: uuid.UUID):
        self.queue = asyncio.Queue()
        try:
            yield self.queue
        finally:
            pass


async def test_stream_backfills_then_delivers_live_signal() -> None:
    user_id = uuid.uuid4()
    chat = SimpleNamespace(principal=SimpleNamespace(tenant_id="t1", platform_user_id=user_id))
    hub = _Hub(watermark=1)
    service = _Service()
    stream = _notification_frames(chat, service, hub, 0, 1.0)  # type: ignore[arg-type]

    backfill = await asyncio.wait_for(anext(stream), timeout=1)
    assert backfill.startswith("id: 1\n")
    assert service.calls[0] == (0, 100)

    assert hub.queue is not None
    hub.queue.put_nowait(
        NotificationSignal(
            tenant_id="t1",
            user_id=user_id,
            notification_id=uuid.uuid4(),
            seq=2,
        )
    )
    live = await asyncio.wait_for(anext(stream), timeout=1)
    assert live.startswith("id: 2\n")
    await stream.aclose()


async def test_live_signal_carries_full_dto_without_backend_fetch() -> None:
    user_id = uuid.uuid4()
    chat = SimpleNamespace(principal=SimpleNamespace(tenant_id="t1", platform_user_id=user_id))
    hub = _Hub(watermark=0)

    class _NoFetch:
        """水位已追平（cursor >= watermark）：建流与 live 都不得回源 runtime。"""

        def __init__(self) -> None:
            self.calls = 0

        async def list_notifications(self, *args, **kwargs) -> _Page:
            self.calls += 1
            raise AssertionError("水位已追平时不得回源 runtime")

    service = _NoFetch()
    stream = _notification_frames(chat, service, hub, 0, 1.0)  # type: ignore[arg-type]
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.01)
    assert hub.queue is not None
    now = datetime.now(UTC).isoformat()
    hub.queue.put_nowait(
        NotificationSignal(
            tenant_id="t1",
            user_id=user_id,
            notification_id=uuid.uuid4(),
            seq=3,
            notification={
                "id": str(uuid.uuid4()),
                "seq": 3,
                "entry_id": str(uuid.uuid4()),
                "agent_id": str(uuid.uuid4()),
                "conversation_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "task_id": None,
                "event_id": None,
                "kind": "RUN_COMPLETED",
                "summary": "会话有新结果",
                "attachments": [],
                "created_at": now,
                "read_at": None,
            },
        )
    )
    frame = await asyncio.wait_for(pending, timeout=1)
    assert frame.startswith("id: 3\n")
    assert '"unread_count":1' in frame
    assert service.calls == 0, "水位已追平时建流与 live 都不得回源"
    await stream.aclose()


async def test_unknown_watermark_falls_back_to_bounded_backfill() -> None:
    """Redis 水位缺失/读失败（None）不能当成已追平：必须回填持久通知。"""
    user_id = uuid.uuid4()
    chat = SimpleNamespace(principal=SimpleNamespace(tenant_id="t1", platform_user_id=user_id))
    hub = _Hub(watermark=None)
    service = _Service()

    stream = _notification_frames(chat, service, hub, 0, 1.0)  # type: ignore[arg-type]
    frame = await asyncio.wait_for(anext(stream), timeout=1)
    assert frame.startswith("id: 1\n")
    assert service.calls == [(0, 100)]
    await stream.aclose()


async def test_snapshot_ready_skips_backfill_when_watermark_is_unknown() -> None:
    """客户端刚做过 REST 快照时，Redis 水位缺失也不必再回填（1000 条流的建流成本）。"""
    user_id = uuid.uuid4()
    chat = SimpleNamespace(principal=SimpleNamespace(tenant_id="t1", platform_user_id=user_id))
    hub = _Hub(watermark=None)

    class _NoFetch:
        async def list_notifications(self, *args, **kwargs):
            raise AssertionError("snapshot_ready 时不得回填")

    stream = _notification_frames(  # type: ignore[arg-type]
        chat, _NoFetch(), hub, 7, 1.0, snapshot_ready=True
    )
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.01)
    assert hub.queue is not None
    now = datetime.now(UTC).isoformat()
    hub.queue.put_nowait(
        NotificationSignal(
            tenant_id="t1",
            user_id=user_id,
            notification_id=uuid.uuid4(),
            seq=8,
            notification={
                "id": str(uuid.uuid4()),
                "seq": 8,
                "entry_id": str(uuid.uuid4()),
                "agent_id": str(uuid.uuid4()),
                "conversation_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "task_id": None,
                "event_id": None,
                "kind": "RUN_COMPLETED",
                "summary": "会话有新结果",
                "attachments": [],
                "created_at": now,
                "read_at": None,
            },
        )
    )
    frame = await asyncio.wait_for(pending, timeout=1)
    assert frame.startswith("id: 8\n")
    await stream.aclose()


async def test_stream_heartbeats_without_events() -> None:
    user_id = uuid.uuid4()
    chat = SimpleNamespace(principal=SimpleNamespace(tenant_id="t1", platform_user_id=user_id))
    hub = _Hub(watermark=0)

    class _Empty:
        async def list_notifications(self, *args, **kwargs) -> _Page:
            return _Page([], next_seq=0, through_seq=0)

    stream = _notification_frames(chat, _Empty(), hub, 0, 0.01)  # type: ignore[arg-type]
    frame = await asyncio.wait_for(anext(stream), timeout=1)
    assert frame == ": heartbeat\n\n"
    await stream.aclose()
