"""Persistence helpers for egress summaries, replay gaps and bounded retention."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.runtime import EgressAuditGap, EgressInterfaceSummary
from .egress_observability import EgressAttemptEvent, aggregate_interface_events


async def upsert_interface_summaries(
    sessions: async_sessionmaker[AsyncSession], events: Iterable[EgressAttemptEvent]
) -> int:
    rows = aggregate_interface_events(events)
    if not rows:
        return 0
    async with sessions.begin() as session:
        for row in rows:
            values = {
                "create_time": row.bucket_start,
                "update_time": row.bucket_start,
                "is_deleted": False,
                "tenant_id": row.tenant_id,
                "target_instance_id": row.target_instance_id,
                "route_id": row.route_id,
                "bucket_start": row.bucket_start,
                "attempted": row.attempted,
                "allowed": row.allowed,
                "sent": row.sent,
                "completed": row.completed,
                "blocked": row.blocked,
                "failed": row.failed,
                "latency_total_ms": row.latency_total_ms,
            }
            stmt = pg_insert(EgressInterfaceSummary).values(id=uuid4(), **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tenant_id", "target_instance_id", "route_id", "bucket_start"],
                set_={
                    key: getattr(stmt.excluded, key)
                    for key in values
                    if key not in {"tenant_id", "target_instance_id", "route_id", "bucket_start"}
                },
            )
            await session.execute(stmt)
    return len(rows)


async def record_egress_gap(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    window_start: datetime,
    window_end: datetime,
    lost_count: int,
    reason: str,
) -> None:
    if lost_count <= 0 or window_end <= window_start:
        raise ValueError("gap window and count must be positive")
    async with sessions.begin() as session:
        stmt = (
            pg_insert(EgressAuditGap)
            .values(
                id=uuid4(),
                create_time=window_end,
                update_time=window_end,
                is_deleted=False,
                tenant_id=tenant_id,
                window_start=window_start,
                window_end=window_end,
                lost_count=lost_count,
                reason=reason,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "window_start", "window_end"],
                set_={"lost_count": EgressAuditGap.lost_count + lost_count, "reason": reason},
            )
        )
        await session.execute(stmt)


async def cleanup_egress_observability(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    detail_days: int = 7,
    summary_days: int = 90,
) -> tuple[int, int]:
    if detail_days <= 0 or summary_days <= detail_days:
        raise ValueError("retention windows are invalid")
    moment = now or datetime.now(UTC)
    async with sessions.begin() as session:
        details = await session.execute(
            sa.delete(EgressInterfaceSummary).where(
                EgressInterfaceSummary.bucket_start < moment - timedelta(days=summary_days)
            )
        )
        gap_result = await session.execute(
            sa.delete(EgressAuditGap).where(EgressAuditGap.window_end < moment - timedelta(days=summary_days))
        )
        audit_cutoff = moment - timedelta(days=detail_days)
        from ..infrastructure.models.runtime import EgressAudit

        audits = await session.execute(sa.delete(EgressAudit).where(EgressAudit.create_time < audit_cutoff))
    removed_details = getattr(details, "rowcount", 0) or 0
    removed_gaps = getattr(gap_result, "rowcount", 0) or 0
    removed_audits = getattr(audits, "rowcount", 0) or 0
    return removed_audits + removed_gaps, removed_details
