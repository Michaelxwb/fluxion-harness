"""Canonical Event 写入：conversation.last_seq 行锁单调分配，事件 append-only。"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import canonical_json
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
        source_event_id: uuid.UUID | None = None,
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
                source_event_id=source_event_id,
            )
        )
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
