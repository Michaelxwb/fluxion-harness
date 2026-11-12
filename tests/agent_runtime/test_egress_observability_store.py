"""E-04 lightweight PG persistence checks; project-wide logging/time suites stay deferred."""

from datetime import UTC, datetime

import pytest
from muad_agent_runtime.application.egress_observability import EgressAttemptEvent
from muad_agent_runtime.application.egress_observability_store import (
    cleanup_egress_observability,
    record_egress_gap,
    upsert_interface_summaries,
)
from muad_agent_runtime.infrastructure.models.runtime import EgressAuditGap, EgressInterfaceSummary
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _event(attempt: str, second: int = 1) -> EgressAttemptEvent:
    return EgressAttemptEvent(
        tenant_id="obs-store-tenant",
        attempt_id=attempt,
        phase="completed",
        target_instance_id="platform",
        route_id="GET /assets",
        occurred_at=datetime(2026, 1, 1, 12, 0, second, tzinfo=UTC),
        result_status="COMPLETED",
        allowed=True,
        sent=True,
        latency_ms=7,
    )


async def test_summary_upsert_and_gap_merge(async_tool_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(async_tool_database, expire_on_commit=False)
    await upsert_interface_summaries(sessions, [_event("a"), _event("a")])
    await record_egress_gap(
        sessions,
        tenant_id="obs-store-tenant",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        lost_count=2,
        reason="spool_overflow",
    )
    await record_egress_gap(
        sessions,
        tenant_id="obs-store-tenant",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        lost_count=3,
        reason="retry",
    )
    async with sessions() as session:
        summary = (
            await session.execute(
                select(EgressInterfaceSummary).where(EgressInterfaceSummary.tenant_id == "obs-store-tenant")
            )
        ).scalar_one()
        gap = (
            await session.execute(
                select(EgressAuditGap).where(EgressAuditGap.tenant_id == "obs-store-tenant")
            )
        ).scalar_one()
    assert summary.attempted == 1 and summary.sent == 1
    assert gap.lost_count == 5 and gap.reason == "retry"


async def test_summary_cleanup_uses_ninety_day_boundary(async_tool_database):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(async_tool_database, expire_on_commit=False)
    await upsert_interface_summaries(sessions, [_event("old")])
    removed, summaries = await cleanup_egress_observability(
        sessions,
        now=datetime(2026, 4, 2, tzinfo=UTC),
        detail_days=7,
        summary_days=90,
    )
    assert removed >= 0 and summaries >= 1
