"""Canonical Event 写入：conversation.last_seq 行锁单调分配，事件 append-only。"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, Conversation


class EventWriter:
    """事务内使用；随事务提交/回滚，回滚不留事件、不推 seq。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> int:
        seq = await self._session.scalar(
            sa.update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.is_deleted.is_(False),
            )
            .values(last_seq=Conversation.last_seq + 1, update_time=sa.func.now())
            .returning(Conversation.last_seq)
        )
        if seq is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        self._session.add(
            CanonicalEvent(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                submission_id=submission_id,
                stream_type=stream_type,
                event_type=event_type,
                payload_json=payload,
                artifact_id=artifact_id,
            )
        )
        return int(seq)

    async def list_events(
        self,
        conversation_id: uuid.UUID,
        *,
        submission_id: uuid.UUID | None = None,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[CanonicalEvent]:
        """历史查询：seq 升序稳定；可按 submission 过滤（重放）。"""
        conditions = [
            CanonicalEvent.conversation_id == conversation_id,
            CanonicalEvent.seq > after_seq,
        ]
        if submission_id is not None:
            conditions.append(CanonicalEvent.submission_id == submission_id)
        rows = await self._session.execute(
            sa.select(CanonicalEvent)
            .where(*conditions)
            .order_by(CanonicalEvent.seq)
            .limit(limit)
        )
        return list(rows.scalars().all())
