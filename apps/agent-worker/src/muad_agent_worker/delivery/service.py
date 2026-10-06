from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from ..application.task_events import TaskEventSeed, TaskEventType, append_event, append_events
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
#: **还会被投递的状态：只有 PENDING**。`FAILED` 是终态（4xx / 路由缺失 / 次数耗尽），
#: 此前把「可重试失败」也写成 FAILED，于是耗尽预算的租户永远留在检测集合里，又按最早
#: `create_time` 排在最前面，把后面所有租户挡死（2026-10-06 评审 #9）。可重试失败现在
#: 写 PENDING —— 与 `DELIVERY_METRIC` 本来就没断过的口径一致（design docs/10：**超过
#: 次数才置 FAILED**）。
DELIVERABLE_STATUSES = (str(DeliveryStatus.PENDING),)
#: 一轮内最多为多少个「有待投递记录」的租户取快照（投递队列跨租户、设置按租户）。
MAX_TENANTS_PER_TICK = 8
DELIVERY_ATTEMPT_TOTAL = "delivery_attempt_total"
DELIVERY_FAILED_TOTAL = "delivery_failed_total"
#: 产物引用解析不到（404）：退文本形态的计数——**看得见**才不会被当成"本来就没产物"
ARTIFACT_GONE_TOTAL = "delivery_artifact_gone_total"
#: 预留之后多久没回执就认定「那次尝试随进程一起没了」（评审 #12）。取值远大于一次
#: HTTP 投递的超时，避免把**正在发送**的记录误判成滞留。
DELIVERY_RESERVATION_LEASE_SEC = 300


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
        #: 跨租户公平游标 `(该租户最早待投递记录的 create_time, tenant_id)`：每轮从这一格
        #: **之后**继续取，取空则回到开头。见 `_pending_tenants`。
        self._tenant_cursor: tuple[datetime, str] | None = None

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
        for tenant_id, first_seen in await self._pending_tenants():
            # 游标只记「这一格已经看过了」：无论它有没有可投递候选，下一轮都从这里往后走。
            # 于是「前 N 个租户的记录都在退避窗口里」不会永远挡住后面的租户（评审 #9）。
            self._tenant_cursor = (first_seen, tenant_id)
            platform = await self._platform_for(tenant_id)
            if platform is None:
                # 一个租户读不到设置，不能连坐整条跨租户队列（评审 #10）。
                continue
            await self._settle_stranded(moment, tenant_id, platform.task)
            candidate = await self._reserve_candidate(
                moment, tenant_id=tenant_id, task_settings=platform.task
            )
            if candidate is None:
                continue
            return await self._attempt_delivery(*candidate, platform=platform, now=moment)
        return None

    async def _platform_for(self, tenant_id: str) -> PlatformSettings | None:
        """取该租户的平台设置；失败**只影响这个租户**，且仍是 fail-closed（不猜默认值）。

        此前异常直接冒到 `run_forever` 的外层：较老的租户读设置失败，后面所有健康租户这一轮
        以及下一轮（同样的顺序）全部停摆（评审 #10 实测：设置调用列表里只有坏租户）。失败
        本身已经由设置客户端记进
        `platform_settings_fetch_total{caller="worker",result="failed"}`；游标保证坏租户每轮
        最多被重试一次，不会挤占健康租户的名额。
        """
        try:
            snapshot = await self._settings_client.fetch_snapshot(tenant_id=tenant_id)
            return resolve_platform_settings(
                snapshot, batch_platform_limit=self._settings.batch_platform_limit
            )
        except Exception:
            logger.warning(
                "delivery_platform_settings_unavailable tenant_id=%s", tenant_id, exc_info=True
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

    async def _pending_tenants(self) -> list[tuple[str, datetime]]:
        """有可投递记录的租户 `[(tenant_id, 该租户最早待投递记录的 create_time)]`。

        投递队列**跨租户**、而设置按租户，故先按设置无关谓词选出待投递租户，再逐个取快照
        判断是否到期；`MAX_TENANTS_PER_TICK` 让「某租户的记录停在退避窗口里」不会让整条
        队列空转，也不会无限放大取快照的次数。

        **游标是这里唯一的公平性来源**（评审 #9）：固定取「最早的前 8 个」时，只要有 8 个
        租户的记录永远选不出候选（例如次数耗尽），后面的租户就永远轮不到。现在每轮从上一
        轮看过的**下一格**继续（按 `(create_time, tenant_id)` 排序），取空就回到开头——窗口
        永远在前进，最坏情况下每个租户每 `租户数/8` 轮被看一次。
        """
        rows = await self._query_pending_tenants(after=self._tenant_cursor)
        if not rows and self._tenant_cursor is not None:
            self._tenant_cursor = None
            rows = await self._query_pending_tenants(after=None)
        return [(row[0], row[1]) for row in rows]

    async def _query_pending_tenants(self, *, after: tuple[datetime, str] | None) -> list[Any]:
        first_seen = sa.func.min(TaskExecution.create_time).label("first_seen")
        statement = (
            select(TaskExecution.tenant_id, first_seen)
            .where(*self._detectable_conditions())
            .group_by(TaskExecution.tenant_id)
            .order_by(first_seen.asc(), TaskExecution.tenant_id.asc())
            .limit(MAX_TENANTS_PER_TICK)
        )
        if after is not None:
            seen_at, seen_tenant = after
            statement = statement.having(
                or_(
                    first_seen > seen_at,
                    sa.and_(first_seen == seen_at, TaskExecution.tenant_id > seen_tenant),
                )
            )
        async with self._session_factory() as session:
            return list((await session.execute(statement)).all())

    async def _settle_stranded(
        self, moment: datetime, tenant_id: str, task_settings: TaskSettings
    ) -> int:
        """结算「预留后崩溃」的滞留记录（评审 #12）。

        `_reserve_candidate` 在发送**之前**就自增 `delivery_attempts` 并提交（崩溃不丢退避
        进度，这是对的），但它同时把状态留在 PENDING：若这次正好用掉最后一次预算，崩溃后
        候选条件 `delivery_attempts < max` 永远为假——既不重投，也没有任何路径把它结算成
        FAILED，记录就永远停在 PENDING。超过预留租约仍未回执的，判定为「那次尝试随进程没
        了」，这里直接终态失败。
        """
        stale_before = moment - timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC)
        async with self._session_factory() as session:
            async with session.begin():
                settled = (
                    await session.execute(
                        update(TaskExecution)
                        .where(
                            *self._detectable_conditions(),
                            TaskExecution.tenant_id == tenant_id,
                            TaskExecution.delivery_attempts
                            >= task_settings.delivery_max_attempts,
                            TaskExecution.update_time < stale_before,
                        )
                        .values(
                            delivery_status=str(DeliveryStatus.FAILED),
                            update_time=moment,
                        )
                        .returning(TaskExecution.id, TaskExecution.tenant_id)
                        .execution_options(synchronize_session=False)
                    )
                ).all()
                if not settled:
                    return 0
                await append_events(
                    session,
                    [
                        TaskEventSeed(
                            tenant_id=row_tenant,
                            task_id=task_id,
                            event_type=TaskEventType.DELIVERY_FAILED,
                            payload={"terminal": True, "reason": "reservation_abandoned"},
                        )
                        for task_id, row_tenant in settled
                    ],
                )
        increment(DELIVERY_FAILED_TOTAL, len(settled))
        record_outcome(DELIVERY_METRIC, str(DeliveryStatus.FAILED))
        logger.warning(
            "delivery_reservation_abandoned tenant_id=%s count=%d", tenant_id, len(settled)
        )
        return len(settled)

    @staticmethod
    def _detectable_conditions() -> tuple[Any, ...]:
        """设置无关的「这条记录还想被投递」谓词（退避/上限判断留到取到快照后）。"""
        return (
            TaskExecution.status.in_(TERMINAL_STATUSES),
            TaskExecution.delivery_mode == str(DeliveryMode.FINAL_ONLY),
            TaskExecution.delivery_status.in_(DELIVERABLE_STATUSES),
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
                "delivery_status": str(
                    DeliveryStatus.FAILED if exhausted else DeliveryStatus.PENDING
                ),
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
                        TaskExecution.delivery_status.in_(DELIVERABLE_STATUSES),
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
