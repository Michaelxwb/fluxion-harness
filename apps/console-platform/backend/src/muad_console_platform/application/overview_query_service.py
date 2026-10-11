"""Build the complete Overview from one frozen instant and platform calendar window."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from muad_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.overview_models import OverviewPayload
from ..infrastructure.repositories.overview_query_repository import OverviewQueryRepository
from .audit_query_service import format_console_time
from .platform_settings_service import PlatformSettingsService

logger = get_logger(__name__)


def overview_now() -> datetime:
    return datetime.now(UTC)


class OverviewQueryService:
    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        self._session = session
        self._repository = OverviewQueryRepository(session)
        self._tenant_id = tenant_id

    async def get_overview(self, *, days: int = 7) -> OverviewPayload:
        now = overview_now()
        timezone = await self._display_timezone()
        zone = ZoneInfo(timezone)
        start_day = now.astimezone(zone).date() - timedelta(days=days - 1)
        start = datetime.combine(start_day, datetime.min.time(), zone)
        metrics = await self._repository.aggregate(self._tenant_id, start=start, now=now, timezone=timezone)
        generated_at = format_console_time(now, timezone=zone)
        assert generated_at is not None
        return OverviewPayload(
            **metrics.model_dump(), days=days, timezone=timezone, generated_at=generated_at
        )

    async def _display_timezone(self) -> str:
        snapshot = await PlatformSettingsService(self._session).read_current(self._tenant_id)
        candidate = snapshot.settings.locale.default_timezone
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError) as error:
            logger.warning("overview_timezone_invalid timezone=%s error=%s", candidate, error)
            return "UTC"
        return candidate
