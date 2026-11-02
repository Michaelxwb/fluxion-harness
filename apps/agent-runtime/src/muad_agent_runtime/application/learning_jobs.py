"""学习 job runner：claim、lease 回收、回放与终态。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_contracts.platform_settings import LearningSettings

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import LearningJob
from ..metrics import LEARNING_PIPELINE_FAILURES_METRIC, record_counter
from .learning_replay import INCOMPLETE, LearningReplay, ReplayLimits

logger = logging.getLogger(__name__)


class LearningJobRunner:
    def __init__(
        self,
        factory: SessionFactory,
        replay: LearningReplay,
        *,
        settings_provider: Callable[[str], Awaitable[LearningSettings]],
        max_attempts: int = 3,
        lease_sec: float = 300.0,
        poll_interval_sec: float = 5.0,
    ) -> None:
        self._factory = factory
        self._replay = replay
        self._settings = settings_provider
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_sec)
        self._interval = poll_interval_sec
        self._task: asyncio.Task[None] | None = None

    async def _claim(self) -> LearningJob | None:
        now = datetime.now(UTC)
        async with self._factory.begin() as session:
            row = await session.execute(
                sa.text(
                    "UPDATE runtime.learning_job SET status='RUNNING',"
                    " attempts=attempts+1, lease_until=:lease, update_time=now()"
                    " WHERE id = ("
                    "   SELECT id FROM runtime.learning_job"
                    "    WHERE is_deleted=false AND (status='PENDING'"
                    "      OR (status='RUNNING' AND lease_until < :now))"
                    "    ORDER BY create_time FOR UPDATE SKIP LOCKED LIMIT 1"
                    " ) RETURNING id"
                ),
                {"lease": now + self._lease, "now": now},
            )
            job_id = row.scalar_one_or_none()
            if job_id is None:
                return None
            claimed = await session.scalar(
                sa.select(LearningJob).where(LearningJob.id == job_id)
            )
            return claimed

    async def _finish(
        self,
        job: LearningJob,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry: bool = False,
    ) -> None:
        async with self._factory.begin() as session:
            row = await session.get(LearningJob, job.id)
            if row is None:
                return
            row.status = "PENDING" if retry else status
            row.lease_until = None
            row.result_json = result
            row.error_code = error_code
            row.error_message = (error_message or "")[:512] or None
            row.update_time = datetime.now(UTC)

    async def run_once(self) -> bool:
        job = await self._claim()
        if job is None:
            return False
        settings: LearningSettings = await self._settings(job.tenant_id)
        limits = ReplayLimits(
            min_cases=settings.eval_min_cases,
            max_cases=settings.eval_max_cases,
            min_improvement=settings.eval_min_improvement,
        )
        try:
            decision = await self._replay.run(
                job.tenant_id,
                str((job.request_json or {}).get("task_family", "")),
                experience_key=job.experience_key,
                experience_user_id=job.user_id,
                base_config_hash=str((job.request_json or {}).get("base_config_hash", "")),
                limits=limits,
            )
            await self._finish(
                job,
                status="SUCCEEDED",
                result={
                    "status": decision.status,
                    "improvement": decision.improvement,
                    "incomplete": decision.status == INCOMPLETE,
                },
            )
        except Exception as exc:  # noqa: BLE001 - job 失败必须留现场且可重试
            record_counter(
                LEARNING_PIPELINE_FAILURES_METRIC, labels={"stage": "job", "outcome": "ERROR"}
            )
            logger.warning(
                "learning_job_failed",
                extra={"job_id": str(job.id), "error_type": type(exc).__name__},
            )
            retry = job.attempts < self._max_attempts
            await self._finish(
                job,
                status="FAILED",
                error_code=type(exc).__name__,
                error_message=str(exc),
                retry=retry,
            )
        return True

    async def run_forever(self) -> None:
        while True:
            try:
                while await self.run_once():
                    pass
            except Exception:  # noqa: BLE001 - 循环自愈
                logger.warning("learning_job_loop_failed")
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="learning-jobs")
