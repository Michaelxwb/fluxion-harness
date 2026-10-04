"""压缩审计事件的读写（FEAT-05 / FEAT-08 / RULE-05）。

两种事件都落在既有的 `runtime.canonical_event`（复用 `EventWriter` 的 `conversation.last_seq`
行锁分配，不另造 seq 链；沿用 `canonical_event(run_id, seq)` 既有索引）：

- `CONTEXT_SUMMARY`：摘要**作为权威历史**落库，带它覆盖到的 `covers_up_to_seq` 与 transcript 引用；
- `CONTEXT_COMPACTED`：压缩**真的发生**时的审计行（各层 fired / 组数 / 省下字节）。

重建语义：取 `seq` 最大的一份 `CONTEXT_SUMMARY` 作前缀，其后再按 seq 顺序应用后续事件，最后
跑前三层——因为前三层是纯函数、摘要是库里的权威事实，重放是确定性的（FEAT-08）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent
from .run_events import EventWriter

CONTEXT_SUMMARY_EVENT = "CONTEXT_SUMMARY"
CONTEXT_COMPACTED_EVENT = "CONTEXT_COMPACTED"


async def record_summary(
    writer: EventWriter,
    *,
    tenant_id: str,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    covers_up_to_seq: int,
    summary: Mapping[str, Any],
    transcript_artifact_id: str | None,
    bytes_before: int,
    bytes_after: int,
    submission_id: uuid.UUID | None = None,
) -> int:
    """摘要事件：`covers_up_to_seq` 表示"seq ≤ 该值的原始事件已被这份摘要覆盖"。"""
    return await writer.append(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        event_type=CONTEXT_SUMMARY_EVENT,
        payload={
            "covers_up_to_seq": covers_up_to_seq,
            "summary": dict(summary),
            "transcript_artifact_id": transcript_artifact_id,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
        },
        submission_id=submission_id,
    )


async def record_compaction(
    writer: EventWriter,
    *,
    tenant_id: str,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    layers: Sequence[Mapping[str, Any]],
    summary_event_seq: int | None = None,
    submission_id: uuid.UUID | None = None,
) -> int:
    """压缩审计事件：各层的 `{layer, fired, groups, bytes_saved}` 原样落库。"""
    payload: dict[str, Any] = {
        "layers": {str(item["layer"]): dict(item) for item in layers},
    }
    if summary_event_seq is not None:
        payload["summary_event_seq"] = summary_event_seq
    return await writer.append(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        event_type=CONTEXT_COMPACTED_EVENT,
        payload=payload,
        submission_id=submission_id,
    )


async def latest_summary(
    session: AsyncSession, tenant_id: str, conversation_id: uuid.UUID
) -> CanonicalEvent | None:
    """最新一份摘要事件——重建时的前缀来源（按 `seq` 取最大，不是按时间戳）。"""
    row = await session.scalar(
        sa.select(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == tenant_id,
            CanonicalEvent.conversation_id == conversation_id,
            CanonicalEvent.event_type == CONTEXT_SUMMARY_EVENT,
            CanonicalEvent.is_deleted.is_(False),
        )
        .order_by(CanonicalEvent.seq.desc())
        .limit(1)
    )
    return row


def covered_up_to(payload: Mapping[str, Any] | None) -> int:
    """摘要事件覆盖到的 seq；形状不对时返回 0（等于"什么都没覆盖"），不抛。"""
    value = (payload or {}).get("covers_up_to_seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "CONTEXT_COMPACTED_EVENT",
    "CONTEXT_SUMMARY_EVENT",
    "covered_up_to",
    "latest_summary",
    "record_compaction",
    "record_summary",
]
