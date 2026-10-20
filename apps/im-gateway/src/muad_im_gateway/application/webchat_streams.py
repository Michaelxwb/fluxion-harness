"""公开 SSE 流：游标、分片与缓冲回压（design §3.4 API-07、§3.6、§3.7；TASK-061）。

职责与边界：

- **完整转 Owner、网关不持库**：事件流来自 Runtime 的持久事件端点（API-04，TASK-060 的共享追尾
  与 `check_cursor` 就在那里），游标分型（`CURSOR_EXPIRED`/`CURSOR_AHEAD`）由 Owner 裁决、原样上抛；
  归属与**当前**入口/Agent 授权经 Owner 接口核验后才打开流（与资源面同一门控口径）。
- **公开投影（公开边）**：canonical `stream_type` → TASK-006 契约的公开 DTO 只在这里发生；
  工具参数/结果、模型请求、推理、SYSTEM、记忆与未知事件**默认不公开**（过滤后 canonical seq
  允许跳号，不补号、不重排、不判丢包）。已知公开事件的坏载荷显式失败，不伪装成过滤。
- **安全 checkpoint**：需要推进过滤水位时下发 `checkpoint`，只带**真实已扫描水位**（严格小于下一条
  公开事件的 seq），不分配 canonical seq、不追加正文；心跳无 `id`，不推进业务游标。
- **分片**：`message.snapshot`（整组替换正文）超过单帧预算即按 `part_index/part_count` 分片，
  同 `event_id`/`seq`/`reply_generation`；片间不插入其他事件，全部到齐才可原子替换。无法分片的
  超限帧（例如大增量）**显式关闭订阅**，绝不截断或静默丢弃。
- **缓冲回压**：每个订阅者一个**有界**缓冲（`sse_subscriber_buffer_bytes`），进程总缓冲也有限
  （`sse_total_buffer_bytes`）；到界即显式关闭该慢订阅（warning + 计数），客户端按持久游标重连
  补齐——不丢正文、不重建 Run（design §3.7「超额关闭慢订阅并依持久游标恢复」）。
- **公开身份**：canonical 行 id 不出现在 Owner 流封套里，公开事件身份由 `(run_id, canonical seq)`
  确定性派生（同一事件在重连/重放中得到同一 `event_id`，客户端据此按 run/event 去重）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.webchat import WebchatAttachment, WebchatConfirmation, WebchatExecutionStatus
from muad_contracts.webchat_events import (
    PublicWebchatEvent,
    WebchatArtifactEvent,
    WebchatCheckpoint,
    WebchatCheckpointData,
    WebchatConfirmationEvent,
    WebchatDeltaEvent,
    WebchatErrorEvent,
    WebchatReplyDelta,
    WebchatReplySnapshot,
    WebchatSafeError,
    WebchatSnapshotEvent,
    WebchatStageEvent,
    WebchatTerminal,
    WebchatTerminalEvent,
)
from pydantic import ValidationError

from ..metrics import (
    SseCloseReason,
    SseSubscriptionStatus,
    record_sse_close,
    record_sse_frame,
    record_sse_rejection,
    record_subscription,
    set_ledger_gauge,
)
from .chat_principal import ChatIdentity
from .sse import SseEvent
from .webchat_service import owned_run_state, required_uuid

logger = logging.getLogger(__name__)

#: canonical `stream_type` → 公开事件类型（TASK-006 契约的判别联合）；未知/内部默认不公开。
PUBLIC_STREAM_EVENT_TYPES: dict[str, str] = {
    "message.delta": "message.delta",
    "message.snapshot": "message.snapshot",
    "run.stage": "run.stage",
    "run.completed": "run.terminal",
    "run.failed": "run.error",
    "artifact.created": "artifact.created",
    "interrupt.required": "confirmation.required",
    "memory.confirmation_required": "confirmation.required",
}
#: 公开事件身份的派生命名空间：canonical 行 id 不在 Owner 流封套里，公开身份只由 (run, seq) 派生。
PUBLIC_IDENTITY_NAMESPACE = uuid.UUID("2c9d4f6a-5b18-4e73-9a20-7d1f3b8c6e54")
#: 心跳帧：注释帧，无 `id`/`event`（心跳不推进业务游标，浏览器与网关解析器都跳过它）。
HEARTBEAT_FRAME = ": heartbeat\n\n"
SSE_MEDIA_TYPE = "text/event-stream"
#: SSE 响应头：私有数据 no-store + 关闭入口代理的响应缓冲（design §3.4 前端设计 §3.2）。
SSE_STREAM_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
#: 分片容量收敛的重试次数（每次按真实溢出量收缩；再不合就是配置不可能满足，显式失败）。
_SPLIT_ATTEMPTS = 4


class WebchatFrameError(RuntimeError):
    """公开帧无法安全下发：显式关闭订阅，绝不截断、绝不伪装成过滤。"""


class SseSubscriberLimitExceeded(RuntimeError):
    """进程订阅者数量上限（`GATEWAY_MAX_SSE_SUBSCRIBERS`）：显式拒绝，不排队也不静默丢。"""


class MalformedPublicEvent(WebchatFrameError):
    """已知公开事件的坏载荷（canonical 生产者坏了，不静默当过滤）。"""


class OversizedPublicFrame(WebchatFrameError):
    """单帧超过预算且不可分片（分片只对整组替换的 snapshot 合法）。"""


@dataclass(frozen=True, slots=True)
class WebchatStreamBudget:
    """公开流的启动传输预算（`SharedSettings`，装配根注入；进程启动后固定）。"""

    sse_event_max_bytes: int
    sse_heartbeat_sec: float
    sse_subscriber_buffer_bytes: int
    sse_total_buffer_bytes: int

    def __post_init__(self) -> None:
        for name, value in (
            ("sse_event_max_bytes", self.sse_event_max_bytes),
            ("sse_subscriber_buffer_bytes", self.sse_subscriber_buffer_bytes),
            ("sse_total_buffer_bytes", self.sse_total_buffer_bytes),
        ):
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.sse_heartbeat_sec, bool) or self.sse_heartbeat_sec <= 0:
            raise ValueError("sse_heartbeat_sec must be a positive number")
        if self.sse_total_buffer_bytes < self.sse_subscriber_buffer_bytes:
            raise ValueError("total buffer must cover at least one subscriber buffer")


@dataclass(frozen=True, slots=True)
class PublicFrame:
    """一帧公开 SSE：`id` 游标 + 公开事件类型 + 公开 DTO JSON + 整帧字节数。"""

    cursor: int
    event_type: str
    payload_json: str
    frame_bytes: int

    def render(self) -> str:
        return f"id: {self.cursor}\nevent: {self.event_type}\ndata: {self.payload_json}\n\n"


@dataclass(frozen=True, slots=True)
class WebchatSnapshotGroup:
    """一次可分片的公开 snapshot：整组替换某一代正文，分片只在这里发生。"""

    event_id: UUID
    conversation_id: UUID
    run_id: UUID
    seq: int
    timestamp: datetime
    reply_generation: int
    text: str


#: 一次 canonical 行可产出的公开投影：单个公开事件，或需要按帧预算分片的 snapshot 整组。
PublicProjection = PublicWebchatEvent | WebchatSnapshotGroup


def public_event_id(run_id: UUID, seq: int) -> UUID:
    """公开事件身份：`(run_id, canonical seq)` 的确定性派生（重连/重放得到同一 id）。"""
    return uuid.uuid5(PUBLIC_IDENTITY_NAMESPACE, f"muad:webchat:public:{run_id}:{seq}")


def _cursor_status(code: str) -> SseSubscriptionStatus:
    """打开 Owner 流失败的订阅结局：只把游标分型当独立样本，其余一律 `failed`（不猜测）。"""
    if code == ErrorCode.CURSOR_EXPIRED:
        return "CURSOR_EXPIRED"
    if code == ErrorCode.CURSOR_AHEAD:
        return "CURSOR_AHEAD"
    return "failed"


def _aware_timestamp(raw: str | None) -> datetime:
    """canonical 帧时间戳 → 感知时区时间；缺失/非法只影响展示字段，用网关当前时刻补齐。"""
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _identity(
    *, event_id: UUID, conversation_id: UUID, run_id: UUID, seq: int, timestamp: datetime
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "seq": seq,
        "timestamp": timestamp,
    }


def _frame(cursor: int, event_type: str, payload_json: str) -> PublicFrame:
    rendered = f"id: {cursor}\nevent: {event_type}\ndata: {payload_json}\n\n"
    return PublicFrame(
        cursor=cursor,
        event_type=event_type,
        payload_json=payload_json,
        frame_bytes=len(rendered.encode("utf-8")),
    )


def _checkpoint_projection(
    *, conversation_id: UUID, run_id: UUID, cursor: int, timestamp: datetime
) -> WebchatCheckpoint:
    """安全 checkpoint：水位是真实已扫描 seq，时间戳是那一行的真实时间（不另分配 canonical seq）。"""
    return WebchatCheckpoint(
        type="checkpoint",
        conversation_id=conversation_id,
        run_id=run_id,
        seq=cursor,
        timestamp=timestamp,
        data=WebchatCheckpointData(),
    )


class WebchatPublicProjector:
    """canonical Owner 帧 → 公开契约 DTO（默认不公开；已知公开事件的坏载荷显式失败）。"""

    def __init__(self, *, run_id: UUID, conversation_id: UUID) -> None:
        self._run_id = run_id
        self._conversation_id = conversation_id

    def project(self, event: SseEvent) -> PublicProjection | None:
        """公开投影：非本 Run/无 canonical 序号/未知或内部类型一律 None（合法过滤）。"""
        if event.seq is None or event.seq < 1 or event.run_id != str(self._run_id):
            return None
        public_type = PUBLIC_STREAM_EVENT_TYPES.get(event.type)
        if public_type is None:
            return None
        payload = event.data if isinstance(event.data, dict) else {}
        timestamp = _aware_timestamp(event.timestamp)
        if public_type == "message.snapshot":
            return self._snapshot(payload, event.seq, timestamp)
        if public_type in ("confirmation.required", "artifact.created"):
            return self._optional(public_type, payload, event.seq, timestamp)
        try:
            return self._build(public_type, payload, event.seq, timestamp)
        except ValidationError as exc:
            raise MalformedPublicEvent(
                f"stream_type={event.type} seq={event.seq}"
            ) from exc

    def _build(
        self, public_type: str, payload: dict[str, Any], seq: int, timestamp: datetime
    ) -> PublicWebchatEvent:
        """白名单内的公开事件：字段逐项显式取值，内部载荷字段没有位置。"""
        identity = _identity(
            event_id=public_event_id(self._run_id, seq),
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            seq=seq,
            timestamp=timestamp,
        )
        if public_type == "message.delta":
            return WebchatDeltaEvent(
                type="message.delta",
                data=WebchatReplyDelta.model_validate(
                    {
                        "reply_generation": payload.get("reply_generation", 0),
                        "text": payload.get("delta"),
                    }
                ),
                **identity,
            )
        if public_type == "run.stage":
            return WebchatStageEvent(
                type="run.stage",
                data=WebchatExecutionStatus.model_validate(payload),
                **identity,
            )
        if public_type == "run.terminal":
            return WebchatTerminalEvent(
                type="run.terminal",
                data=WebchatTerminal.model_validate({"status": payload.get("status")}),
                **identity,
            )
        if public_type == "run.error":
            return WebchatErrorEvent(
                type="run.error",
                data=WebchatSafeError.model_validate(
                    {
                        "code": payload.get("error_code"),
                        "retryable": payload.get("retryable", False),
                    }
                ),
                **identity,
            )
        raise MalformedPublicEvent(f"unmapped public type {public_type}")

    def _snapshot(
        self, payload: dict[str, Any], seq: int, timestamp: datetime
    ) -> WebchatSnapshotGroup | None:
        """整组替换正文的 snapshot：坏形状显式失败（它是正文替换，不能静默丢）。"""
        try:
            part = WebchatReplySnapshot.model_validate(
                {
                    "reply_generation": payload.get("reply_generation", 0),
                    "text": payload.get("text"),
                    "part_index": 0,
                    "part_count": 1,
                }
            )
        except ValidationError as exc:
            raise MalformedPublicEvent(f"stream_type=message.snapshot seq={seq}") from exc
        return WebchatSnapshotGroup(
            event_id=public_event_id(self._run_id, seq),
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            seq=seq,
            timestamp=timestamp,
            reply_generation=part.reply_generation,
            text=part.text,
        )

    def _optional(
        self, public_type: str, payload: dict[str, Any], seq: int, timestamp: datetime
    ) -> PublicProjection | None:
        """可选公开事实：只有载荷本身就是完整安全 DTO 才公开（坏形状不报错也不透传）。"""
        identity = _identity(
            event_id=public_event_id(self._run_id, seq),
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            seq=seq,
            timestamp=timestamp,
        )
        try:
            if public_type == "confirmation.required":
                return WebchatConfirmationEvent(
                    type="confirmation.required",
                    data=WebchatConfirmation.model_validate(payload),
                    **identity,
                )
            return WebchatArtifactEvent(
                type="artifact.created",
                data=WebchatAttachment.model_validate(payload),
                **identity,
            )
        except ValidationError:
            return None


def _snapshot_part(group: WebchatSnapshotGroup, text: str, index: int, count: int) -> PublicFrame:
    event = WebchatSnapshotEvent(
        type="message.snapshot",
        event_id=group.event_id,
        conversation_id=group.conversation_id,
        run_id=group.run_id,
        seq=group.seq,
        timestamp=group.timestamp,
        data=WebchatReplySnapshot(
            reply_generation=group.reply_generation,
            text=text,
            part_index=index,
            part_count=count,
        ),
    )
    return _frame(group.seq, "message.snapshot", event.model_dump_json())


def _split_text(text: str, capacity: int) -> list[str]:
    """按 UTF-8 字节容量切分文本（字符边界切分：不切碎多字节字符、不丢字符）。"""
    parts: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for char in text:
        size = len(char.encode("utf-8"))
        if current and current_bytes + size > capacity:
            parts.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += size
    if current:
        parts.append("".join(current))
    return parts or [""]


def render_public_event(projection: PublicProjection, budget: WebchatStreamBudget) -> list[PublicFrame]:
    """公开投影 → 帧：snapshot 按单帧预算分片；其余事件一帧一事件（超限显式失败）。"""
    if isinstance(projection, WebchatSnapshotGroup):
        return _snapshot_frames(projection, budget)
    payload_json = projection.model_dump_json()
    frame = _frame(projection.seq, projection.type, payload_json)
    if frame.frame_bytes > budget.sse_event_max_bytes:
        raise OversizedPublicFrame(
            f"type={projection.type} seq={projection.seq} bytes={frame.frame_bytes}"
        )
    return [frame]


def _snapshot_frames(group: WebchatSnapshotGroup, budget: WebchatStreamBudget) -> list[PublicFrame]:
    """分片：同 seq 同 event_id 的整组，逐片都在单帧预算内（容量按真实溢出量收敛）。"""
    limit = budget.sse_event_max_bytes
    capacity = limit - _snapshot_part(group, "", 0, 1).frame_bytes
    for _ in range(_SPLIT_ATTEMPTS):
        if capacity < 1:
            raise OversizedPublicFrame(f"snapshot budget too small: seq={group.seq}")
        texts = _split_text(group.text, capacity)
        frames = [_snapshot_part(group, text, index, len(texts)) for index, text in enumerate(texts)]
        overflow = max((frame.frame_bytes - limit for frame in frames), default=0)
        if overflow <= 0:
            return frames
        capacity -= overflow
    raise OversizedPublicFrame(f"snapshot cannot fit the frame budget: seq={group.seq}")


class WebchatPublicScanner:
    """canonical 帧序列 → 公开投影 + 安全 checkpoint（已扫描水位只在真实行上推进）。"""

    def __init__(self, *, run_id: UUID, conversation_id: UUID) -> None:
        self._projector = WebchatPublicProjector(run_id=run_id, conversation_id=conversation_id)
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._cursor = 0
        self._scanned = 0
        self._scanned_at: datetime | None = None

    def projections(self, event: SseEvent) -> list[PublicProjection]:
        """一条 canonical 帧 → 投影序列：过滤行只推进已扫描水位（需要时先下发 checkpoint）。"""
        seq = event.seq
        if seq is None or seq < 1 or seq <= self._cursor:
            return []
        previous_scanned = self._scanned
        self._scanned = max(self._scanned, seq)
        self._scanned_at = _aware_timestamp(event.timestamp)
        projection = self._projector.project(event)
        if projection is None:
            return []
        out: list[PublicProjection] = []
        # checkpoint 只推进到**下一条公开事件之前**：绝不越过待应用的公开行（否则客户端会去重丢掉它）。
        pending = min(previous_scanned, seq - 1)
        if pending > self._cursor:
            out.append(self._checkpoint(pending))
        self._cursor = seq
        out.append(projection)
        return out

    def pending_checkpoint(self) -> WebchatCheckpoint | None:
        """流结束时把已扫描的过滤尾部推进给客户端（不追加正文、不越过任何公开行）。"""
        if self._scanned <= self._cursor:
            return None
        return self._checkpoint(self._scanned)

    def _checkpoint(self, cursor: int) -> WebchatCheckpoint:
        return _checkpoint_projection(
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            cursor=cursor,
            timestamp=self._scanned_at or datetime.now(UTC),
        )


@dataclass(slots=True)
class SseLedgerStats:
    """公开流的可观测快照：下发/过滤/分片/关闭/拒绝都留痕（不静默）。"""

    active_subscribers: int = 0
    total_pending_bytes: int = 0
    max_pending_bytes: int = 0
    max_total_pending_bytes: int = 0
    frames_sent: int = 0
    heartbeats: int = 0
    checkpoints: int = 0
    filtered_events: int = 0
    snapshot_parts: int = 0
    slow_subscriber_closed: int = 0
    total_buffer_closed: int = 0
    malformed_closed: int = 0
    oversized_closed: int = 0
    owner_stream_failed_closed: int = 0
    rejected_subscribers: int = 0


class SseBufferLedger:
    """进程级公开流缓冲账本：进程总缓冲与订阅者数量都显式有界（单订阅份额由流层裁决）。"""

    def __init__(self, *, total_bytes: int, max_subscribers: int) -> None:
        if total_bytes < 1:
            raise ValueError("total buffer must be positive")
        if max_subscribers < 1:
            raise ValueError("subscriber limit must be positive")
        self._total_bytes = total_bytes
        self._max_subscribers = max_subscribers
        self._pending = 0
        self._active = 0
        self._stats = SseLedgerStats()

    def lease(self) -> SseBufferLease:
        """登记一个订阅者：到进程订阅上限即**显式拒绝**（不静默丢、不排队）。"""
        if self._active >= self._max_subscribers:
            self._stats.rejected_subscribers += 1
            record_subscription("rejected")
            record_sse_rejection()
            logger.warning(
                "webchat_sse_subscriber_limit_rejected active=%s limit=%s",
                self._active,
                self._max_subscribers,
            )
            raise SseSubscriberLimitExceeded(
                f"active subscribers {self._active} reached limit {self._max_subscribers}"
            )
        self._active += 1
        self._publish_watermarks()
        return SseBufferLease(self)

    def charge(self, size: int) -> bool:
        """进程总缓冲占用：超额返回 False（调用方必须显式关闭该订阅，不静默丢帧）。"""
        if self._pending + size > self._total_bytes:
            return False
        self._pending += size
        self._stats.max_total_pending_bytes = max(
            self._stats.max_total_pending_bytes, self._pending
        )
        self._publish_watermarks()
        return True

    def release(self, size: int) -> None:
        self._pending = max(0, self._pending - size)
        self._publish_watermarks()

    def retire(self) -> None:
        self._active = max(0, self._active - 1)
        self._publish_watermarks()

    def _publish_watermarks(self) -> None:
        """发布进程级水位：活跃订阅数与未下发字节（连接与缓冲都从真实账本读出）。"""
        set_ledger_gauge("active_subscribers", self._active)
        set_ledger_gauge("pending_bytes", self._pending)

    def note_pending(self, pending: int) -> None:
        self._stats.max_pending_bytes = max(self._stats.max_pending_bytes, pending)

    def note_sent(self, frame: PublicFrame) -> None:
        self._stats.frames_sent += 1
        if frame.event_type == "checkpoint":
            self._stats.checkpoints += 1
            record_sse_frame("checkpoint")
        elif frame.event_type == "message.snapshot":
            self._stats.snapshot_parts += 1
            record_sse_frame("snapshot_part")
        else:
            record_sse_frame("event")

    def note_filtered(self) -> None:
        self._stats.filtered_events += 1
        record_sse_frame("filtered")

    def note_heartbeat(self) -> None:
        self._stats.heartbeats += 1
        record_sse_frame("heartbeat")

    def note_closed(self, reason: SseCloseReason) -> None:
        """显式关闭：每条路径都留痕（词表外的原因显式失败，不静默吞）。"""
        if reason == "slow_subscriber":
            self._stats.slow_subscriber_closed += 1
            record_sse_close("slow_subscriber")
        elif reason == "total_buffer":
            self._stats.total_buffer_closed += 1
            record_sse_close("total_buffer")
        elif reason == "malformed":
            self._stats.malformed_closed += 1
            record_sse_close("malformed")
        elif reason == "oversized":
            self._stats.oversized_closed += 1
            record_sse_close("oversized")
        elif reason == "owner_stream_failed":
            self._stats.owner_stream_failed_closed += 1
            record_sse_close("owner_stream_failed")
        else:
            raise ValueError(f"unknown public SSE close reason {reason!r}")

    def stats(self) -> SseLedgerStats:
        return replace(
            self._stats, active_subscribers=self._active, total_pending_bytes=self._pending
        )


class SseBufferLease:
    """一个订阅者的缓冲租约：占用计入进程总预算，退出即释放（幂等）。"""

    def __init__(self, ledger: SseBufferLedger) -> None:
        self._ledger = ledger
        self._closed = False

    def charge(self, size: int) -> bool:
        return self._ledger.charge(size)

    def release(self, size: int) -> None:
        self._ledger.release(size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._ledger.retire()


class WebchatPublicStream:
    """一次公开订阅：Owner 事件流 → 公开帧 → 有界缓冲 → HTTP 字节（心跳在消费侧产出）。"""

    def __init__(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        owner_events: AsyncIterator[SseEvent],
        owner_close: AsyncExitStack,
        budget: WebchatStreamBudget,
        ledger: SseBufferLedger,
        lease: SseBufferLease,
    ) -> None:
        self._run_id = run_id
        self._budget = budget
        self._ledger = ledger
        self._lease = lease
        self._scanner = WebchatPublicScanner(run_id=run_id, conversation_id=conversation_id)
        self._owner_events = owner_events
        self._owner_close = owner_close
        self._queue: asyncio.Queue[PublicFrame | None] = asyncio.Queue()
        self._pending = 0
        self._producer: asyncio.Task[None] | None = None

    async def frames(self) -> AsyncIterator[str]:
        """HTTP 侧字节：有帧即下发（到界即关闭），空闲按 `sse_heartbeat_sec` 发心跳。"""
        self._producer = asyncio.create_task(self._produce())
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        self._queue.get(), timeout=self._budget.sse_heartbeat_sec
                    )
                except TimeoutError:
                    self._ledger.note_heartbeat()
                    yield HEARTBEAT_FRAME
                    continue
                if frame is None:
                    return
                self._pending -= frame.frame_bytes
                self._lease.release(frame.frame_bytes)
                self._ledger.note_sent(frame)
                yield frame.render()
        finally:
            await self._shutdown()

    async def _produce(self) -> None:
        """上游读取：投影 → 分片 → 入有界缓冲；任何无法安全下发的帧都显式关闭订阅。"""
        try:
            async for event in self._owner_events:
                projections = self._scanner.projections(event)
                if not projections:
                    self._ledger.note_filtered()
                    continue
                for projection in projections:
                    if not await self._emit(projection):
                        return
            pending = self._scanner.pending_checkpoint()
            if pending is not None:
                await self._emit(pending)
        except WebchatFrameError as exc:
            if isinstance(exc, MalformedPublicEvent):
                self._ledger.note_closed("malformed")
            else:
                self._ledger.note_closed("oversized")
            logger.warning("webchat_sse_frame_rejected reason=%s detail=%s", type(exc).__name__, exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 上游流中断：显式关闭订阅，客户端按游标重连
            self._ledger.note_closed("owner_stream_failed")
            logger.warning("webchat_sse_owner_stream_failed", exc_info=True)
        finally:
            self._queue.put_nowait(None)

    async def _emit(self, projection: PublicProjection) -> bool:
        for frame in render_public_event(projection, self._budget):
            if not self._offer(frame):
                return False
        return True

    def _offer(self, frame: PublicFrame) -> bool:
        """入缓冲：进程总预算与单订阅预算都有界，到界即显式关闭（返回 False），不静默丢帧。

        检查顺序 = 预算严格程度：**进程总预算先判**（它是全进程的硬上界），再判单订阅份额；
        两者相等时说明进程已被这一条订阅占满，计数按总预算留痕。
        """
        size = frame.frame_bytes
        if not self._lease.charge(size):
            self._ledger.note_closed("total_buffer")
            logger.warning(
                "webchat_sse_total_buffer_closed pending=%s limit=%s",
                self._pending,
                self._budget.sse_total_buffer_bytes,
            )
            return False
        if self._pending + size > self._budget.sse_subscriber_buffer_bytes:
            self._lease.release(size)  # 订阅即将关闭：不把这一笔占用带进进程预算
            self._ledger.note_closed("slow_subscriber")
            logger.warning(
                "webchat_sse_slow_subscriber_closed pending=%s limit=%s",
                self._pending,
                self._budget.sse_subscriber_buffer_bytes,
            )
            return False
        self._pending += size
        self._ledger.note_pending(self._pending)
        self._queue.put_nowait(frame)
        return True

    async def _shutdown(self) -> None:
        """收尾：停上游、**释放本订阅尚未下发的缓冲占用**、释放租约（账本不漂移）。"""
        producer, self._producer = self._producer, None
        if producer is not None and not producer.done():
            producer.cancel()
        if producer is not None:
            with suppress(asyncio.CancelledError):
                await producer
        if self._pending:
            self._lease.release(self._pending)
            self._pending = 0
        self._lease.close()
        await self._owner_close.aclose()


def _require_mapping(container: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Owner 封套里的必需对象：缺失/类型不对是内部契约破坏，显式失败而不是猜。"""
    value = container.get(key)
    if not isinstance(value, dict):
        logger.warning(
            "webchat_sse_scope_invalid",
            extra={"key": key, "keys": sorted(container)},
        )
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return value


class WebchatStreamOwnerPort(Protocol):
    """公开流要的 Owner 能力（Runtime 是唯一实现；结构替身只需实现同一动词面）。"""

    async def get_run_scope(
        self, run_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]:
        """一次解析 Run 归属 + 会话入口/Agent（会话由 Run 行导出）。"""
        ...

    def open_event_stream(
        self, run_id: UUID, *, tenant_id: str, after_seq: int = 0, trace_id: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[SseEvent]]: ...


class WebchatStreamService:
    """API-07 应用服务：归属/当前授权核验 + 公开流打开（游标分型由 Owner 裁决、原样上抛）。"""

    def __init__(
        self,
        *,
        runtime: WebchatStreamOwnerPort,
        tenant_id: str,
        budget: WebchatStreamBudget,
        ledger: SseBufferLedger,
    ) -> None:
        self._runtime = runtime
        self._tenant_id = tenant_id
        self._budget = budget
        self._ledger = ledger

    async def open(self, identity: ChatIdentity, run_id: UUID, *, after_seq: int) -> WebchatPublicStream:
        """门控后的完整转 Owner：一次核 Run 归属 + 会话 Agent 当前授权 → 开订阅。

        订阅者数量上限在**打开 Owner 流之前**裁决：到界显式拒绝（429），不占上游连接、
        不排队等待；打开失败时释放刚取得的租约，不留半开连接。
        游标越界/过期由 Owner 的 `check_cursor` 显式失败（`CURSOR_AHEAD`/`CURSOR_EXPIRED`），
        网关不夹取、不猜测。
        """
        try:
            return await self._open(identity, run_id, after_seq=after_seq)
        except AppError as exc:
            logger.warning("webchat_sse_open_error", extra={"code": exc.code})
            raise
        except Exception as exc:  # noqa: BLE001 —— 观测兜底：未处理异常必须留痕后原样上抛
            logger.warning("webchat_sse_open_unexpected", extra={"error": type(exc).__name__})
            raise

    async def _open(self, identity: ChatIdentity, run_id: UUID, *, after_seq: int) -> WebchatPublicStream:
        try:
            scope = await self._runtime.get_run_scope(
                run_id,
                tenant_id=self._tenant_id,
                user_id=identity.principal.platform_user_id,
            )
        except AppError as exc:
            logger.warning("webchat_sse_scope_rejected", extra={"code": exc.code})
            raise
        state = owned_run_state(identity, _require_mapping(scope, "run"))
        conversation_id = required_uuid(state, "conversation_id")
        agent_id = required_uuid(_require_mapping(scope, "conversation"), "agent_id")
        self._assert_agent_authorized(identity, agent_id)
        lease = self._acquire_lease()
        stack = AsyncExitStack()
        try:
            events = await stack.enter_async_context(
                self._runtime.open_event_stream(
                    run_id, tenant_id=self._tenant_id, after_seq=max(0, after_seq)
                )
            )
        except AppError as exc:
            # Owner 的游标分型原样留痕（`CURSOR_EXPIRED`/`CURSOR_AHEAD` 不冒充普通失败）。
            logger.warning("webchat_sse_open_rejected", extra={"code": exc.code})
            record_subscription(_cursor_status(exc.code))
            lease.close()
            await stack.aclose()
            raise
        except BaseException:
            record_subscription("failed")
            lease.close()
            await stack.aclose()
            raise
        # 断线重连（`after_seq`/`Last-Event-ID` > 0）与新开是不同样本：恢复能力可观测。
        if after_seq > 0:
            record_subscription("resumed")
        else:
            record_subscription("opened")
        return WebchatPublicStream(
            run_id=run_id,
            conversation_id=conversation_id,
            owner_events=events,
            owner_close=stack,
            budget=self._budget,
            ledger=self._ledger,
            lease=lease,
        )

    def _acquire_lease(self) -> SseBufferLease:
        """订阅者数量上限：到界显式拒绝（429），绝不静默丢或让请求悬挂。"""
        try:
            return self._ledger.lease()
        except SseSubscriberLimitExceeded as exc:
            raise AppError(ErrorCode.RUN_CAPACITY_EXCEEDED) from exc

    def _assert_agent_authorized(self, identity: ChatIdentity, agent_id: UUID) -> None:
        """会话的 Agent 必须在**当前**授权集合内（撤销影响后续 SSE 访问，与读取面同口径）。"""
        if agent_id not in {agent.agent_id for agent in identity.agents}:
            raise AppError(ErrorCode.FORBIDDEN)
