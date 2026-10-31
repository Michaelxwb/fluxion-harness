"""Implicit correction signals derived from conversation events (offline, best-effort).

信号只作候选证据与 TRIAL 监控，不直接驱动激活；因此分类器是确定性的词法规则，
误判的代价是"多一条证据"，不是改变行为。检测失败不得影响 consolidation job 或主 Run。
"""

from __future__ import annotations

import logging
from uuid import UUID

import sqlalchemy as sa
from muad_agent_core.memory.tokens import tokenize

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord
from .run_events import EventWriter

logger = logging.getLogger(__name__)

LEARNING_SIGNAL_EVENT = "LEARNING_SIGNAL"
NEGATION = "NEGATION"
REASK = "REASK"
REWRITE = "REWRITE"

NEGATION_MARKERS = (
    "不对",
    "错了",
    "不是这个",
    "重新",
    "再来",
    "改一下",
    "修正",
    "有问题",
    "wrong",
    "not what",
    "redo",
    "again",
    "no,",
)
REASK_THRESHOLD = 0.9
REWRITE_THRESHOLD = 0.6


def _overlap(previous: str, current: str) -> float:
    left, right = tokenize(previous), tokenize(current)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def classify_signal(previous_user: str, previous_assistant: str, current_user: str) -> str | None:
    text = current_user.strip()
    if not text:
        return None
    lowered = text.casefold()
    if any(marker in lowered for marker in NEGATION_MARKERS):
        return NEGATION
    overlap = _overlap(previous_user, text)
    length_ratio = len(text) / len(previous_user) if previous_user else 0.0
    if overlap >= REASK_THRESHOLD and 0.9 <= length_ratio <= 1.1:
        return REASK
    if overlap >= REWRITE_THRESHOLD:
        return REWRITE
    return None


def _text(row: CanonicalEvent | None) -> str:
    if row is None:
        return ""
    value = row.payload_json.get("text") if isinstance(row.payload_json, dict) else None
    return value if isinstance(value, str) else ""


async def detect_and_write_signal(
    factory: SessionFactory,
    *,
    tenant_id: str,
    conversation_id: UUID,
    run_id: UUID,
) -> str | None:
    """为本次 Run 检测一次隐式信号；同一 Run 至多一条，返回 signal_type 或 None。"""
    async with factory() as session:
        existing = await session.scalar(
            sa.select(CanonicalEvent.id).where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.event_type == LEARNING_SIGNAL_EVENT,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
        if existing is not None:
            return None
        rows = (
            await session.scalars(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.event_type.in_(("USER_MESSAGE", "ASSISTANT_MESSAGE")),
                    CanonicalEvent.is_deleted.is_(False),
                )
                .order_by(CanonicalEvent.seq)
            )
        ).all()

        current: CanonicalEvent | None = None
        previous_user: CanonicalEvent | None = None
        previous_assistant: CanonicalEvent | None = None
        for row in rows:
            if current is not None:
                break
            if row.event_type == "USER_MESSAGE" and row.run_id == run_id:
                current = row
                break
            if row.event_type == "USER_MESSAGE":
                previous_user = row
            else:
                previous_assistant = row
        if current is None or previous_user is None:
            return None

        signal_type = classify_signal(
            _text(previous_user), _text(previous_assistant), _text(current)
        )
        if signal_type is None:
            return None
        run = await session.get(RunRecord, run_id)
        if run is None:
            return None
        await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=LEARNING_SIGNAL_EVENT,
            payload={
                "tenant_id": tenant_id,
                "user_id": str(run.user_id),
                "source_run_id": str(previous_user.run_id) if previous_user.run_id else None,
                "signal_type": signal_type,
                "user_seq": current.seq,
            },
        )
        await session.commit()
    return signal_type
