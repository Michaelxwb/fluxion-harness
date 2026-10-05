from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import (
    AttachmentRef,
    DeliveryMode,
    DeliveryRequest,
    DeliveryRouteInput,
    DeliveryStatus,
    TaskStatus,
)
from muad_contracts.platform_settings import PlatformSettings, TaskSettings
from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.platform_settings import resolve_platform_settings
from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from ..application.task_events import TaskEventType, append_event
from ..infrastructure.models.task import DeliveryRoute, TaskExecution
from ..metrics import DELIVERY_METRIC, increment, record_outcome
from .artifact_client import (
    ArtifactGoneError,
    ArtifactResolveClient,
    ArtifactResolveError,
)
from .client import DeliveryClientProtocol, DeliveryResult, DeliveryTransportError
from .messages import build_delivery_message

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (str(TaskStatus.COMPLETED), str(TaskStatus.FAILED), str(TaskStatus.CANCELLED))
RETRYABLE_DELIVERY_STATUSES = (str(DeliveryStatus.PENDING), str(DeliveryStatus.FAILED))
#: 一轮内最多为多少个「有待投递记录」的租户取快照（投递队列跨租户、设置按租户）。
MAX_TENANTS_PER_TICK = 8
DELIVERY_ATTEMPT_TOTAL = "delivery_attempt_total"
DELIVERY_FAILED_TOTAL = "delivery_failed_total"
#: 产物引用解析不到（404）：退文本形态的计数——**看得见**才不会被当成"本来就没产物"
ARTIFACT_GONE_TOTAL = "delivery_artifact_gone_total"


@dataclass(frozen=True)
class DeliveryOutcome:
    task_id: uuid.UUID
    http_status: int | None
    error: str | None
    sent: bool


class DeliveryLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: DeliveryClientProtocol,
        settings: SharedSettings | None = None,
        resolver: ArtifactResolveClient | None = None,
        *,
        settings_client: PlatformSettingsClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        #: 产物引用解析（TASK-006）：worker 只有不透明的 artifact_id，解析口径在 runtime 一处
        self._resolver = resolver
        self._settings = settings or SharedSettings()
        self._settings_client: PlatformSettingsClient = (
            settings_client or NullPlatformSettingsClient()
        )

    async def _resolve_artifact(
        self, task: TaskExecution, *, now: datetime, max_attempts: int
    ) -> AttachmentRef | DeliveryOutcome | None:
        """把 `result_artifact_id` 解析成渠道中立的引用。

        **两类失败分开对待**（这是本方法存在的全部理由）：
        - 产物**没了**（404）⇒ 重试多少次都不会好 ⇒ 退回**文本形态**（含任务结论），
          而不是让这条投递永远卡住、也不是给用户一串死 ID；
        - **没拿到结论**（超时/5xx）⇒ 可重试 ⇒ 直接以可重试失败收场，**不**降级成文本：
          降级会把"该发文件却发了串 ID"**固化**下来，而那正是本需求要消灭的东西。
        """
        if task.result_artifact_id is None or self._resolver is None:
            return None
        try:
            return await self._resolver.resolve(task.result_artifact_id, tenant_id=task.tenant_id)
        except ArtifactGoneError:
            increment(ARTIFACT_GONE_TOTAL)
            logger.warning("delivery_artifact_gone task_id=%s", task.id)
            return None
        except ArtifactResolveError as exc:
            await self._record_retryable_failure(
                task, error=f"artifact resolve: {exc}", now=now, max_attempts=max_attempts
            )
            return DeliveryOutcome(
                task_id=task.id, http_status=None, error=f"artifact resolve: {exc}", sent=False
            )

    async def run_forever(self) -> None:
        """每轮最多连续投递 `delivery_batch_size` 条，积压时不再每 poll 间隔只发一条。"""
        while True:
            try:
                for _ in range(self._settings.delivery_batch_size):
                    if await self.run_once() is None:
                        break
            except Exception:
                logger.exception("delivery_loop_tick_failed")
            await asyncio.sleep(self._settings.delivery_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> DeliveryOutcome | None:
        moment = now or datetime.now(UTC)
        # 投递尝试边界：只有确实有可投递记录（设置无关谓词）时才取快照；空闲轮询零调用。
        for tenant_id in await self._pending_tenants():
            snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id)
            platform = resolve_platform_settings(
                snapshot, batch_platform_limit=self._settings.batch_platform_limit
            )
            candidate = await self._reserve_candidate(
                moment, tenant_id=tenant_id, task_settings=platform.task
            )
            if candidate is None:
                continue
            return await self._attempt_delivery(
                *candidate, platform=platform, now=moment
            )
        return None

    async def _attempt_delivery(
        self,
        task: TaskExecution,
        route: DeliveryRoute | None,
        *,
        platform: PlatformSettings,
        now: datetime,
    ) -> DeliveryOutcome:
        max_attempts = platform.task.delivery_max_attempts
        increment(DELIVERY_ATTEMPT_TOTAL)
        if route is None:
            error = "delivery route missing"
            await self._record_terminal_failure(task, error=error, now=now, max_attempts=max_attempts)
            return DeliveryOutcome(task_id=task.id, http_status=None, error=error, sent=False)
        artifact = await self._resolve_artifact(task, now=now, max_attempts=max_attempts)
        if isinstance(artifact, DeliveryOutcome):
            return artifact  # 解析没拿到结论：按可重试失败收场，**不降级成文本**
        request = DeliveryRequest(
            tenant_id=task.tenant_id,
            task_id=task.id,
            delivery_key=task.delivery_key,
            route=_route_input(route),
            message=build_delivery_message(
                task, platform.locale.default_locale, artifact=artifact
            ),
            artifact_ids=[task.result_artifact_id] if task.result_artifact_id else [],
        )
        try:
            result = await self._client.deliver(request)
        except DeliveryTransportError as exc:
            error = str(exc)
            await self._record_retryable_failure(
                task, error=error, now=now, max_attempts=max_attempts
            )
            return DeliveryOutcome(task_id=task.id, http_status=None, error=error, sent=False)
        return await self._interpret_result(task, result, now=now, max_attempts=max_attempts)

    async def _interpret_result(
        self,
        task: TaskExecution,
        result: DeliveryResult,
        *,
        now: datetime,
        max_attempts: int,
    ) -> DeliveryOutcome:
        status_code = result.status_code
        if 200 <= status_code < 300 and result.delivered:
            await self._record_sent(task, now=now)
            return DeliveryOutcome(task_id=task.id, http_status=status_code, error=None, sent=True)
        if 200 <= status_code < 300:
            # Gateway 只占位、未确认送达：不得置 SENT，按可重试失败退避后重试。
            error = "delivery accepted as in-flight placeholder, not delivered yet"
            await self._record_retryable_failure(
                task, error=error, now=now, max_attempts=max_attempts
            )
            return DeliveryOutcome(task_id=task.id, http_status=status_code, error=error, sent=False)
        error = f"delivery returned status {status_code}"
        if 400 <= status_code < 500:
            await self._record_terminal_failure(
                task, error=error, now=now, max_attempts=max_attempts
            )
        else:
            await self._record_retryable_failure(
                task, error=error, now=now, max_attempts=max_attempts
            )
        return DeliveryOutcome(task_id=task.id, http_status=status_code, error=error, sent=False)

    async def _pending_tenants(self) -> list[str]:
        """有可投递记录的租户（按最早记录排序，取有界前缀）。

        投递队列**跨租户**、而设置按租户，故先按设置无关谓词选出待投递租户，再逐个取
        快照判断是否到期；`MAX_TENANTS_PER_TICK` 让「某租户的记录停在退避窗口里」不会
        让整条队列空转，也不会无限放大取快照的次数。
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TaskExecution.tenant_id)
                    .where(*self._detectable_conditions())
                    .group_by(TaskExecution.tenant_id)
                    .order_by(sa.func.min(TaskExecution.create_time).asc())
                    .limit(MAX_TENANTS_PER_TICK)
                )
            ).all()
        return [row[0] for row in rows]

    @staticmethod
    def _detectable_conditions() -> tuple[Any, ...]:
        """设置无关的「这条记录还想被投递」谓词（退避/上限判断留到取到快照后）。"""
        return (
            TaskExecution.status.in_(TERMINAL_STATUSES),
            TaskExecution.delivery_mode == str(DeliveryMode.FINAL_ONLY),
            TaskExecution.delivery_status.in_(RETRYABLE_DELIVERY_STATUSES),
            TaskExecution.is_deleted.is_(False),
        )

    async def _reserve_candidate(
        self,
        moment: datetime,
        *,
        tenant_id: str,
        task_settings: TaskSettings,
    ) -> tuple[TaskExecution, DeliveryRoute | None] | None:
        """先持久预留本次尝试再发送：多副本并发时只有一个能拿到候选。

        `delivery_attempts` 在调用 Gateway 之前自增并提交，因此进程崩溃或重启后
        退避进度与尝试上限都不会丢；重复请求在退避窗口内不会被再次选中。
        退避窗口与尝试上限来自该租户的平台设置快照（本边界取一次）。
        """
        backoff = sa.func.make_interval(
            0,
            0,
            0,
            0,
            0,
            0,
            task_settings.delivery_backoff_base_sec
            * sa.func.power(2, TaskExecution.delivery_attempts),
        )
        candidate_id = (
            select(TaskExecution.id)
            .where(
                *self._detectable_conditions(),
                TaskExecution.tenant_id == tenant_id,
                TaskExecution.delivery_attempts < task_settings.delivery_max_attempts,
                or_(
                    TaskExecution.delivery_attempts == 0,
                    TaskExecution.update_time < moment - backoff,
                ),
            )
            .order_by(TaskExecution.create_time.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            async with session.begin():
                reserved_id = (
                    await session.execute(
                        update(TaskExecution)
                        .where(TaskExecution.id == candidate_id)
                        .values(
                            delivery_attempts=TaskExecution.delivery_attempts + 1,
                            delivery_status=str(DeliveryStatus.PENDING),
                            update_time=moment,
                        )
                        .returning(TaskExecution.id)
                    )
                ).scalar_one_or_none()
                if reserved_id is None:
                    return None
                task = (
                    await session.execute(
                        select(TaskExecution).where(TaskExecution.id == reserved_id)
                    )
                ).scalar_one()
                route = None
                if task.delivery_route_id is not None:
                    route = (
                        await session.execute(
                            select(DeliveryRoute).where(DeliveryRoute.id == task.delivery_route_id)
                        )
                    ).scalar_one_or_none()
        return task, route

    async def _record_sent(self, task: TaskExecution, *, now: datetime) -> None:
        record_outcome(DELIVERY_METRIC, str(DeliveryStatus.SENT))
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.SENT),
                "delivered_at": now,
                "update_time": now,
            },
            event_type=TaskEventType.DELIVERY_SENT,
            payload={"delivery_attempts": task.delivery_attempts},
        )

    async def _record_terminal_failure(
        self,
        task: TaskExecution,
        *,
        error: str,
        now: datetime,
        max_attempts: int,
    ) -> None:
        increment(DELIVERY_FAILED_TOTAL)
        record_outcome(DELIVERY_METRIC, str(DeliveryStatus.FAILED))
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.FAILED),
                "delivery_attempts": max_attempts,
                "update_time": now,
            },
            event_type=TaskEventType.DELIVERY_FAILED,
            payload={"error": error, "terminal": True},
        )

    async def _record_retryable_failure(
        self,
        task: TaskExecution,
        *,
        error: str,
        now: datetime,
        max_attempts: int,
    ) -> None:
        attempts = task.delivery_attempts
        exhausted = attempts >= max_attempts
        if exhausted:
            increment(DELIVERY_FAILED_TOTAL)
        record_outcome(
            DELIVERY_METRIC,
            str(DeliveryStatus.FAILED if exhausted else DeliveryStatus.PENDING),
        )
        await self._apply(
            task,
            values={
                "delivery_status": str(DeliveryStatus.FAILED),
                "delivery_attempts": attempts,
                "update_time": now,
            },
            event_type=(
                TaskEventType.DELIVERY_FAILED if exhausted else TaskEventType.DELIVERY_RETRY
            ),
            payload={"error": error, "delivery_attempts": attempts, "terminal": exhausted},
        )

    async def _apply(
        self,
        task: TaskExecution,
        *,
        values: dict[str, Any],
        event_type: TaskEventType,
        payload: dict[str, Any],
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.id == task.id,
                        TaskExecution.delivery_status.in_(RETRYABLE_DELIVERY_STATUSES),
                        TaskExecution.delivery_attempts == task.delivery_attempts,
                        TaskExecution.is_deleted.is_(False),
                    )
                    .values(**values)
                )
                if int(cast(CursorResult[Any], result).rowcount) == 1:
                    await append_event(
                        session,
                        tenant_id=task.tenant_id,
                        task_id=task.id,
                        event_type=event_type,
                        payload=payload,
                    )


def _route_input(route: DeliveryRoute) -> DeliveryRouteInput:
    return DeliveryRouteInput.model_validate(
        {
            "channel": route.channel,
            "bot_id": route.bot_id,
            "external_user_id": route.external_user_id,
            "external_conversation_id": route.external_conversation_id,
        }
    )
