"""Background learning scheduler: extraction + candidates for enabled tenants.

单实例周期循环（幂等服务）；`learning_enabled=false` 的租户跳过。失败独立降级，
不影响主 Run；TRIAL/回放接线由后续能力在同一循环内扩展。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from muad_contracts.platform_settings import LearningSettings, parse_platform_settings

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import RunRecord
from ..metrics import LEARNING_SCHEDULER_METRIC, LEARNING_PIPELINE_FAILURES_METRIC, record_counter
from .experience_candidates import ExperienceCandidates
from .golden_set import GoldenSetExtractor, GoldenSetLimits
from .ports import PlatformSettingsClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchedulerReport:
    tenants: int
    enabled: int
    extracted: int
    candidates: int
    skipped: int


class LearningSupervisor:
    def __init__(
        self,
        factory: SessionFactory,
        *,
        settings_client: PlatformSettingsClient | None = None,
        extractor: GoldenSetExtractor | None = None,
        candidates: ExperienceCandidates | None = None,
        interval_sec: float = 60.0,
        close_timeout_sec: float = 5.0,
    ) -> None:
        if interval_sec <= 0 or close_timeout_sec <= 0:
            raise ValueError("invalid learning scheduler interval")
        self._factory = factory
        self._settings = settings_client
        self._extractor = extractor or GoldenSetExtractor(factory)
        self._candidates = candidates or ExperienceCandidates(factory)
        self._interval = interval_sec
        self._close_timeout = close_timeout_sec
        self._task: asyncio.Task[None] | None = None

    async def run_once(self, now: datetime | None = None) -> SchedulerReport:
        moment = now or datetime.now(UTC)
        tenants = await self._tenants()
        enabled = extracted = candidates = skipped = 0
        for tenant_id in tenants:
            settings = await self._learning_settings(tenant_id)
            if not settings.learning_enabled:
                skipped += 1
                continue
            enabled += 1
            try:
                report = await self._extractor.extract(tenant_id, now=moment)
                extracted += report.inserted
                candidate_report = await self._candidates.generate(tenant_id, now=moment)
                candidates += candidate_report.created
            except Exception as exc:  # noqa: BLE001 - 学习链路必须降级
                record_counter(
                    LEARNING_PIPELINE_FAILURES_METRIC,
                    labels={"stage": "scheduler", "outcome": "ERROR"},
                )
                logger.warning(
                    "learning_scheduler_tenant_failed",
                    extra={"tenant_id": tenant_id, "error_type": type(exc).__name__},
                )
        return SchedulerReport(len(tenants), enabled, extracted, candidates, skipped)

    async def _tenants(self) -> list[str]:
        async with self._factory() as session:
            rows = await session.scalars(
                sa.select(RunRecord.tenant_id)
                .where(RunRecord.is_deleted.is_(False))
                .distinct()
                .order_by(RunRecord.tenant_id)
            )
            return list(rows.all())

    async def _learning_settings(self, tenant_id: str) -> LearningSettings:
        if self._settings is None:
            return parse_platform_settings({}).learning
        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id)
        return parse_platform_settings(snapshot.settings).learning

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="learning-scheduler")

    async def run_forever(self) -> None:
        while True:
            try:
                report = await self.run_once()
            except Exception as exc:  # noqa: BLE001 - 循环自身不得退出
                record_counter(
                    LEARNING_PIPELINE_FAILURES_METRIC,
                    labels={"stage": "scheduler", "outcome": "ERROR"},
                )
                logger.warning("learning_scheduler_failed", extra={"error_type": type(exc).__name__})
                record_counter(LEARNING_SCHEDULER_METRIC, labels={"outcome": "ERROR"})
            else:
                record_counter(LEARNING_SCHEDULER_METRIC, labels={"outcome": "OK"})
                logger.info(
                    "learning_scheduler_cycle",
                    extra={
                        "tenants": report.tenants,
                        "enabled": report.enabled,
                        "extracted": report.extracted,
                        "candidates": report.candidates,
                    },
                )
            await asyncio.sleep(self._interval)

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=self._close_timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._task = None
