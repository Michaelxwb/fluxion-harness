"""Tenant-scoped read-only Overview aggregation, one consistent SQL snapshot."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .overview_models import OverviewMetrics
from .overview_sql import OVERVIEW_SQL


class OverviewQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def aggregate(
        self, tenant_id: str, *, start: datetime, now: datetime, timezone: str
    ) -> OverviewMetrics:
        result = await self._session.execute(
            text(OVERVIEW_SQL),
            {"tenant_id": tenant_id, "start": start, "now": now, "tz": timezone},
        )
        return OverviewMetrics.model_validate(result.scalar_one())
