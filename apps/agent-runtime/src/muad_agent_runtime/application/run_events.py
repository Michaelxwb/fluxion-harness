"""Canonical Event 写入：conversation.last_seq 行锁单调分配，事件 append-only。"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import canonical_json
from muad_contracts.webchat import WebchatExecutionStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, Conversation

if TYPE_CHECKING:
    from muad_common import SharedSettings

#: 公开阶段事件：`payload_json` 就是 `WebchatExecutionStatus` 的线格式（无内部字段）。
RUN_STAGE_EVENT = "RUN_STAGE"
RUN_STAGE_STREAM_TYPE = "run.stage"
#: 首次生成的回复代号（design §3.6「reply generation」）：多代替换由 TASK-062 由此递增；
#: 增量行因此**总是显式携带**生成代号，不靠投影侧的缺省兜底。
FIRST_REPLY_GENERATION = 0


def delta_payload(text: str, *, reply_generation: int = FIRST_REPLY_GENERATION) -> dict[str, Any]:
    """`message.delta` 的 canonical 载荷：合并后的正文 + 生成代号（公开投影按契约读取）。"""
    return {"delta": text, "reply_generation": reply_generation}


@dataclass(slots=True)
class DeltaWindow:
    """模型增量的**有界**合并窗口：时间或字节到任一边界即应 flush（design §3.6/§3.7）。

    窗口从"缓冲里的第一段增量"起算：到界即返回合并文本，由调用方**先提交再推送**；
    `flush_bytes` 同时是单行增量的上界（单个模型块自身超界时按块落库，**不切分**正文）。
    时钟可注入（生产用 `monotonic`，测试用假时钟）：窗口自身不碰 IO，也不持有事务。
    """

    flush_interval_sec: float
    flush_bytes: int
    clock: Callable[[], float] = time.monotonic
    _parts: list[str] = field(default_factory=list)
    _bytes: int = 0
    _opened_at: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.flush_interval_sec) or self.flush_interval_sec <= 0:
            raise ValueError("flush interval must be a finite positive number")
        if self.flush_bytes < 1:
            raise ValueError("flush bytes must be positive")

    def add(self, text: str) -> str | None:
        """追加一段增量；到任一边界返回合并文本（调用方须先提交再推送），否则返回 None。"""
        if not text:
            return None
        if not self._parts:
            self._opened_at = self.clock()
        self._parts.append(text)
        self._bytes += len(text.encode("utf-8"))
        if self._bytes >= self.flush_bytes or self.due():
            return self.drain()
        return None

    def due(self) -> bool:
        """时间边界是否到界（字节边界由 `add` 直接判定；空窗口永不"到界"）。"""
        if self._opened_at is None:
            return False
        return self.clock() - self._opened_at >= self.flush_interval_sec

    def drain(self) -> str | None:
        """取走全部缓冲（到界 flush 与终态前 flush 共用）；空窗口返回 None，不产出空事件。"""
        if not self._parts:
            return None
        merged = "".join(self._parts)
        self._parts.clear()
        self._bytes = 0
        self._opened_at = None
        return merged

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    def remaining_seconds(self) -> float | None:
        """距时间边界的剩余秒数；空窗口返回 None（调用方按自己的轮询节拍等待）。"""
        if self._opened_at is None:
            return None
        return max(0.0, self.flush_interval_sec - (self.clock() - self._opened_at))


def delta_window_from_settings(settings: SharedSettings) -> DeltaWindow:
    """装配根注入：窗口参数只来自启动设置（design §3.7 的唯一来源，调用点不另立默认）。"""
    return DeltaWindow(
        flush_interval_sec=settings.stream_delta_flush_interval_ms / 1000.0,
        flush_bytes=settings.stream_delta_flush_bytes,
    )


@dataclass(frozen=True, slots=True)
class EventAppend:
    """批量追加的一条事件（与 `EventWriter.append` 同参；同一事务、同一会话）。"""

    tenant_id: str
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    submission_id: uuid.UUID | None = None
    stream_type: str | None = None
    artifact_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None


class EventWriter:
    """事务内使用；随事务提交/回滚，回滚不留事件、不推 seq。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_many(self, entries: Sequence[EventAppend]) -> tuple[int, ...]:
        """批量追加同一会话的事件：一次 `last_seq` 分配 + 每事件一行 INSERT。

        调用方保证全部条目属于同一租户/会话/事务（受理在一个事务内追加多条系统事件）。
        `source_event_id` 的去重与冲突判定与 `append` 完全一致。
        """
        if not entries:
            return ()
        seqs: list[int | None] = [None] * len(entries)
        missing: list[int] = []
        for index, entry in enumerate(entries):
            if entry.source_event_id is None:
                missing.append(index)
                continue
            existing = await self._source_event(
                entry.tenant_id, entry.conversation_id, entry.source_event_id
            )
            if existing is None:
                missing.append(index)
                continue
            if (
                existing.run_id != entry.run_id
                or existing.event_type != entry.event_type
                or canonical_json(existing.payload_json) != canonical_json(entry.payload)
            ):
                raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
            seqs[index] = int(existing.seq)
        if missing:
            allocated = await self._next_seq_range(
                entries[0].tenant_id, entries[0].conversation_id, len(missing)
            )
            for index, seq in zip(missing, allocated, strict=True):
                entry = entries[index]
                self._session.add(
                    CanonicalEvent(
                        id=entry.event_id or uuid.uuid4(),
                        tenant_id=entry.tenant_id,
                        conversation_id=entry.conversation_id,
                        run_id=entry.run_id,
                        seq=seq,
                        submission_id=entry.submission_id,
                        stream_type=entry.stream_type,
                        event_type=entry.event_type,
                        payload_json=entry.payload,
                        artifact_id=entry.artifact_id,
                        source_event_id=entry.source_event_id,
                    )
                )
                seqs[index] = seq
        return tuple(int(seq) for seq in seqs if seq is not None)

    async def _next_seq_range(
        self, tenant_id: str, conversation_id: uuid.UUID, count: int
    ) -> tuple[int, ...]:
        last = await self._session.scalar(
            sa.update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.is_deleted.is_(False),
            )
            .values(last_seq=Conversation.last_seq + count, update_time=sa.func.now())
            .returning(Conversation.last_seq)
        )
        if last is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return tuple(range(int(last) - count + 1, int(last) + 1))

    async def append(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        submission_id: uuid.UUID | None = None,
        stream_type: str | None = None,
        artifact_id: uuid.UUID | None = None,
        source_event_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> int:
        if source_event_id is not None:
            existing = await self._source_event(tenant_id, conversation_id, source_event_id)
            if existing is not None:
                if (
                    existing.run_id != run_id
                    or existing.event_type != event_type
                    or canonical_json(existing.payload_json) != canonical_json(payload)
                ):
                    raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
                return int(existing.seq)
        seq = await self._next_seq(tenant_id, conversation_id)
        self._session.add(
            CanonicalEvent(
                id=event_id or uuid.uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                submission_id=submission_id,
                stream_type=stream_type,
                event_type=event_type,
                payload_json=payload,
                artifact_id=artifact_id,
                source_event_id=source_event_id,
            )
        )
        return int(seq)

    async def _next_seq(self, tenant_id: str, conversation_id: uuid.UUID) -> int:
        seq = await self._session.scalar(
            sa.update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.is_deleted.is_(False),
            )
            .values(last_seq=Conversation.last_seq + 1, update_time=sa.func.now())
            .returning(Conversation.last_seq)
        )
        if seq is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return int(seq)

    async def _source_event(
        self, tenant: str, conversation_id: uuid.UUID, source_id: uuid.UUID
    ) -> CanonicalEvent | None:
        await self._session.execute(
            sa.select(Conversation.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant,
                Conversation.is_deleted.is_(False),
            )
            .with_for_update()
        )
        event: CanonicalEvent | None = await self._session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.source_event_id == source_id,
                CanonicalEvent.tenant_id == tenant,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
        return event

    async def list_events(
        self,
        conversation_id: uuid.UUID,
        *,
        tenant_id: str | None = None,
        submission_id: uuid.UUID | None = None,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[CanonicalEvent]:
        """历史查询：seq 升序稳定；可按 submission 过滤（重放）；租户过滤防止跨租户读取。"""
        conditions = [
            CanonicalEvent.conversation_id == conversation_id,
            CanonicalEvent.seq > after_seq,
        ]
        if tenant_id is not None:
            conditions.append(CanonicalEvent.tenant_id == tenant_id)
        if submission_id is not None:
            conditions.append(CanonicalEvent.submission_id == submission_id)
        rows = await self._session.execute(
            sa.select(CanonicalEvent).where(*conditions).order_by(CanonicalEvent.seq).limit(limit)
        )
        return list(rows.scalars().all())

    async def append_run_stage(
        self,
        *,
        tenant_id: str,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        status: WebchatExecutionStatus,
    ) -> int:
        """阶段投影行：Console 与 Gateway 两个入口读同一行、同一词表（design §3.2）。

        载荷由契约 DTO 直接序列化（`extra=forbid`），工具参数/密钥/原始推理/正文都不经此路；
        与产生它的 Run 行状态在**同一事务**提交（调用方的事务边界）。
        """
        return await self.append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=RUN_STAGE_EVENT,
            stream_type=RUN_STAGE_STREAM_TYPE,
            payload=status.model_dump(mode="json"),
        )
