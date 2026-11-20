"""独立投递：`task_delivery` / `task_delivery_attempt` 是唯一状态源（design §3.2.11 / §3.5.9）。

三条纪律：

1. **先持久后发送**：预留（attempt 自增 + 发送租约 + attempt 行）先提交，再调用网关；
   进程崩溃只丢这一次尝试，退避进度与尝试上限都在库里。
2. **只有明确回执才算送达**：网关封套 `data.delivered=true`（网页是 Runtime 收件箱
   `persisted=true`）才写 SENT；2xx 缺确认、非法回执、超时按失败分类（4xx 终态，
   其余可重试），耗尽尝试上限才终态 FAILED。
3. **投递不改业务终态**：Execution 的状态只由执行侧写；投递失败只落在投递记录上，
   路由永久失效时额外把来源 Schedule 置 `NEEDS_ATTENTION`（说明原因，不无限重试）。

入队有两处（键由 `records` 统一）：终态事务建最终结果投递；Human 问题由本循环**有界
发现**补记——问题先由等待事务持久化，投递边界只读它，所以投递失败回滚不了等待。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import (
    OUTBOUND_CHANNELS,
    AttachmentRef,
    ControlOutboxStatus,
    DeliveryIntent,
    DeliveryRequest,
    DeliveryRouteInput,
    HumanInteractionStatus,
    TaskStatus,
    is_local_stream_channel,
    web_surface_channels_from_settings,
)
from muad_contracts.platform_settings import PlatformSettings, TaskSettings
from muad_contracts.webchat_events import WebchatTaskResultRegistration
from sqlalchemy import CursorResult, false, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from ..application.platform_settings import resolve_platform_settings
from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from ..application.schedule_attention import flag_schedule_attention
from ..application.task_events import (
    EntityType,
    TaskEventSeed,
    TaskEventType,
    append_event,
    append_events,
)
from ..infrastructure.models.runtime_operations import RuntimeResultOutbox
from ..infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskDeliveryAttempt,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)
from ..metrics import (
    DELIVERY_ARTIFACT_GONE_METRIC,
    DELIVERY_ATTEMPT_METRIC,
    DELIVERY_FAILED_METRIC,
    DELIVERY_METRIC,
    increment,
    record_loop_tick,
    record_outcome,
)
from .artifact_client import ArtifactGoneError, ArtifactResolveClient, ArtifactResolveError
from .client import (
    DeliveryClientProtocol,
    DeliveryResult,
    DeliveryTransportError,
    WebchatInboxClientProtocol,
    WebchatInboxReceipt,
)
from .messages import build_delivery_message, build_human_question_message, task_title
from .records import (
    HUMAN_KEY_PREFIX,
    DeliveryKind,
    DeliveryState,
    human_delivery_key,
    interaction_id_from_key,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (str(TaskStatus.COMPLETED), str(TaskStatus.FAILED), str(TaskStatus.CANCELLED))
OPEN = str(HumanInteractionStatus.OPEN)
ACTIVE_ROUTE = "ACTIVE"
PUSH_INTENT = str(DeliveryIntent.PUSH)
PENDING = str(DeliveryState.PENDING)
SENT = str(DeliveryState.SENT)
FAILED = str(DeliveryState.FAILED)
CANCELLED = str(DeliveryState.CANCELLED)
#: 一轮内最多为多少个「有待投递记录」的租户取快照（投递队列跨租户、设置按租户）。
MAX_TENANTS_PER_TICK = 8
#: 一轮内最多补记多少个 Human 问题的投递（发现是有界的，积压靠下一轮继续）。
MAX_QUESTION_ENQUEUES_PER_TICK = 50
#: 一轮内最多作废多少条已经不可能送达的记录（同样有界，积压靠下一轮继续）。
MAX_DEAD_CANCELLATIONS_PER_TICK = 100
#: 预留之后多久没回执就认定「那次尝试随进程一起没了」。取值远大于一次 HTTP 投递的超时，
#: 避免把**正在发送**的记录误判成滞留。
DELIVERY_RESERVATION_LEASE_SEC = 300
#: 路由永久失效的业务码：网关的「bot 不存在 / 未授权」与本地「route 不可用」。
#: 命中即终态失败并把来源 Schedule 置 `NEEDS_ATTENTION`——不猜替代会话，也不无限重试。
ROUTE_UNAVAILABLE_CODES = frozenset({"BOT_NOT_FOUND", "FORBIDDEN", "DELIVERY_ROUTE_UNAVAILABLE"})
CODE_ROUTE_UNAVAILABLE = "DELIVERY_ROUTE_UNAVAILABLE"
CODE_EXECUTION_MISSING = "DELIVERY_EXECUTION_MISSING"
CODE_INTERACTION_MISSING = "DELIVERY_INTERACTION_MISSING"
CODE_INTERACTION_CLOSED = "HUMAN_INTERACTION_CLOSED"
CODE_ATTEMPT_ABANDONED = "DELIVERY_ATTEMPT_ABANDONED"
CODE_TRANSPORT = "DELIVERY_TRANSPORT_ERROR"
CODE_UNCONFIRMED = "DELIVERY_UNCONFIRMED"
CODE_INBOX_UNAVAILABLE = "WEBCHAT_INBOX_UNAVAILABLE"
CODE_UNSUPPORTED_CHANNEL = "DELIVERY_CHANNEL_UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    delivery_id: uuid.UUID
    execution_id: uuid.UUID
    http_status: int | None
    error: str | None
    sent: bool


@dataclass(frozen=True, slots=True)
class _Reserved:
    """一次已提交的发送预留（租约是本次尝试的栅栏：旧租约不得确认）。"""

    delivery_id: uuid.UUID
    tenant_id: str
    execution_id: uuid.UUID
    delivery_route_id: uuid.UUID
    delivery_kind: str
    delivery_key: str
    attempt_no: int
    lease_owner: str
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class _DeliveryContext:
    execution: TaskExecution
    #: `None` = 创建时那条路由已被删除（软删）：显式失败，不猜替代会话。
    route: DeliveryRoute | None
    schedule_name: str | None
    interaction: TaskHumanInteraction | None


@dataclass(frozen=True, slots=True)
class _Failure:
    """本次尝试的失败结论：分类与原因都由这里一次说清（调用方只负责落库）。"""

    code: str
    message: str
    retryable: bool


class DeliveryLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: DeliveryClientProtocol,
        settings: SharedSettings | None = None,
        resolver: ArtifactResolveClient | None = None,
        *,
        settings_client: PlatformSettingsClient | None = None,
        webchat_inbox: WebchatInboxClientProtocol | None = None,
        lease_owner: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        #: 产物引用解析（TASK-006）：worker 只有不透明的 artifact_id，解析口径在 runtime 一处
        self._resolver = resolver
        self._settings = settings or SharedSettings()
        self._settings_client: PlatformSettingsClient = (
            settings_client or NullPlatformSettingsClient()
        )
        #: WEBCHAT 投递的受信入箱端口（TASK-068）：网页送达以 Runtime 的收件箱提交为准。
        self._webchat_inbox = webchat_inbox
        #: 发送租约的所有者：多副本并发时只有持有租约的那一个能确认结果。
        self._owner = lease_owner or f"delivery-{uuid.uuid4()}"
        #: 跨租户公平游标 `(该租户最早待投递记录的 create_time, tenant_id)`：每轮从这一格
        #: **之后**继续取，取空则回到开头。见 `_pending_tenants`。
        self._tenant_cursor: tuple[datetime, str] | None = None

    async def run_forever(self) -> None:
        """每轮最多连续投递 `delivery_batch_size` 条，积压时不再每 poll 间隔只发一条。"""
        while True:
            try:
                for _ in range(self._settings.delivery_batch_size):
                    if await self.run_once() is None:
                        break
            except Exception:
                logger.exception("delivery_loop_tick_failed")
                record_loop_tick("worker.delivery", "failed")
            else:
                record_loop_tick("worker.delivery", "ok")
            await asyncio.sleep(self._settings.delivery_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> DeliveryOutcome | None:
        moment = now or datetime.now(UTC)
        await self._enqueue_human_questions(moment)
        # 已经不可能送达的记录不依赖任何租户设置：全局有界作废，之后才谈候选。
        await self._cancel_dead_deliveries(moment)
        # 投递尝试边界：只有确实有可投递记录（设置无关谓词）时才取快照；空闲轮询零调用。
        for tenant_id, first_seen in await self._pending_tenants():
            # 游标只记「这一格已经看过了」：无论它有没有可投递候选，下一轮都从这里往后走。
            # 于是「前 N 个租户的记录都在退避窗口里」不会永远挡住后面的租户。
            self._tenant_cursor = (first_seen, tenant_id)
            platform = await self._platform_for(tenant_id)
            if platform is None:
                # 一个租户读不到设置，不能连坐整条跨租户队列。
                continue
            await self._settle_stranded(moment, tenant_id, platform.task)
            reserved = await self._reserve_candidate(
                moment, tenant_id=tenant_id, task_settings=platform.task
            )
            if reserved is None:
                continue
            return await self._attempt_delivery(reserved, platform=platform, now=moment)
        return None

    # ------------------------------------------------------------------ 入队（Human 问题）

    async def _enqueue_human_questions(self, moment: datetime) -> int:
        """为 OPEN 的 Human 问题补记投递（有界、幂等）。

        问题由等待事务先持久化（`task_human_interaction`），投递边界只读它并补一条
        `human:{interaction_id}` 投递——同键唯一约束保证重跑不新增；投递失败也回滚不了等待。
        能力判定用共享通道词表：只有 `delivery_intent=PUSH` 且通道在 `OUTBOUND_CHANNELS` 的
        来源 Revision 才推送，网页入口只在任务中心 pull。
        """
        async with self._session_factory() as session:
            async with session.begin():
                rows = (await session.execute(_pending_question_query())).all()
                if not rows:
                    return 0
                inserted = await _insert_question_deliveries(session, rows, moment)
        if inserted:
            logger.info("delivery_human_questions_enqueued count=%d", len(inserted))
        return len(inserted)

    # -------------------------------------------------------------- 租户扫描与公平性

    async def _platform_for(self, tenant_id: str) -> PlatformSettings | None:
        """取该租户的平台设置；失败**只影响这个租户**，且仍是 fail-closed（不猜默认值）。"""
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

    async def _pending_tenants(self) -> list[tuple[str, datetime]]:
        """有可投递记录的租户 `[(tenant_id, 该租户最早待投递记录的 create_time)]`。

        投递队列**跨租户**、而设置按租户，故先按设置无关谓词选出待投递租户，再逐个取快照
        判断是否到期；`MAX_TENANTS_PER_TICK` 让「某租户的记录停在退避窗口里」不会让整条
        队列空转，也不会无限放大取快照的次数。

        **游标是这里唯一的公平性来源**：固定取「最早的前 8 个」时，只要有 8 个租户的记录
        永远选不出候选（例如次数耗尽），后面的租户就永远轮不到。现在每轮从上一轮看过的
        **下一格**继续（按 `(create_time, tenant_id)` 排序），取空就回到开头。
        """
        rows = await self._query_pending_tenants(after=self._tenant_cursor)
        if not rows and self._tenant_cursor is not None:
            self._tenant_cursor = None
            rows = await self._query_pending_tenants(after=None)
        return [(row[0], row[1]) for row in rows]

    async def _query_pending_tenants(self, *, after: tuple[datetime, str] | None) -> list[Any]:
        first_seen = sa.func.min(TaskDelivery.create_time).label("first_seen")
        statement = (
            select(TaskDelivery.tenant_id, first_seen)
            .where(*self._detectable_conditions())
            .group_by(TaskDelivery.tenant_id)
            .order_by(first_seen.asc(), TaskDelivery.tenant_id.asc())
            .limit(MAX_TENANTS_PER_TICK)
        )
        if after is not None:
            seen_at, seen_tenant = after
            statement = statement.having(
                or_(
                    first_seen > seen_at,
                    sa.and_(first_seen == seen_at, TaskDelivery.tenant_id > seen_tenant),
                )
            )
        async with self._session_factory() as session:
            return list((await session.execute(statement)).all())

    # ---------------------------------------------------------------- 结算与作废

    async def _settle_stranded(
        self, moment: datetime, tenant_id: str, task_settings: TaskSettings
    ) -> int:
        """结算「预留后崩溃」的滞留记录。

        预留在实际发送**之前**就自增 `attempt_count` 并提交（崩溃不丢退避进度，这是对的），
        但状态留在 PENDING：若这次正好用掉最后一次预算，候选条件 `attempt_count < max`
        永远为假——既不重投，也没有任何路径把它结算成 FAILED。超过预留租约仍未回执的，
        判定为「那次尝试随进程没了」，直接终态失败。
        """
        stale_before = moment - timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC)
        async with self._session_factory() as session:
            async with session.begin():
                settled = (
                    await session.execute(
                        update(TaskDelivery)
                        .where(
                            TaskDelivery.tenant_id == tenant_id,
                            TaskDelivery.status == PENDING,
                            TaskDelivery.is_deleted.is_(False),
                            TaskDelivery.attempt_count >= task_settings.delivery_max_attempts,
                            TaskDelivery.lease_until.is_not(None),
                            TaskDelivery.lease_until < stale_before,
                        )
                        .values(
                            status=FAILED,
                            last_error_code=CODE_ATTEMPT_ABANDONED,
                            last_error_message="delivery attempt reservation expired",
                            lease_owner=None,
                            lease_until=None,
                            update_time=moment,
                        )
                        .returning(TaskDelivery.id, TaskDelivery.attempt_count)
                        .execution_options(synchronize_session=False)
                    )
                ).all()
                if not settled:
                    return 0
                await self._record_abandoned(session, settled, tenant_id=tenant_id, now=moment)
        increment(DELIVERY_FAILED_METRIC, len(settled))
        record_outcome(DELIVERY_METRIC, FAILED)
        logger.warning(
            "delivery_reservation_abandoned tenant_id=%s count=%d", tenant_id, len(settled)
        )
        return len(settled)

    async def _record_abandoned(
        self,
        session: AsyncSession,
        settled: Any,
        *,
        tenant_id: str,
        now: datetime,
    ) -> None:
        """把滞留预留的 attempt 行与终态事件补上（与状态改写同一事务）。"""
        for delivery_id, attempt_no in settled:
            await self._finish_attempt(
                session,
                delivery_id=delivery_id,
                tenant_id=tenant_id,
                attempt_no=attempt_no,
                status=FAILED,
                code=CODE_ATTEMPT_ABANDONED,
                now=now,
            )
        await append_events(
            session,
            [
                TaskEventSeed(
                    tenant_id=tenant_id,
                    entity_type=EntityType.DELIVERY,
                    entity_id=delivery_id,
                    event_type=TaskEventType.FAILED,
                    occurred_at=now,
                    payload={"reason": "reservation_abandoned", "terminal": True},
                )
                for delivery_id, _attempt_no in settled
            ],
        )

    async def _cancel_dead_deliveries(self, moment: datetime) -> int:
        """把已经不可能送达的记录作废：问题已关闭、执行已被清理。

        不作废它们会永远留在 PENDING——既不会发，也永远占着扫描名额。这一步只看记录本身，
        不依赖任何租户设置，因此全局有界扫描；否则「租户不在候选里」会让这些行永远没人收。
        """
        targets = _dead_delivery_targets()
        async with self._session_factory() as session:
            async with session.begin():
                cancelled = (
                    await session.execute(
                        update(TaskDelivery)
                        .where(TaskDelivery.id.in_(targets))
                        .values(
                            status=CANCELLED,
                            lease_owner=None,
                            lease_until=None,
                            update_time=moment,
                        )
                        .returning(TaskDelivery.id, TaskDelivery.tenant_id)
                        .execution_options(synchronize_session=False)
                    )
                ).all()
                if not cancelled:
                    return 0
                await append_events(
                    session,
                    [
                        TaskEventSeed(
                            tenant_id=tenant_id,
                            entity_type=EntityType.DELIVERY,
                            entity_id=delivery_id,
                            event_type=TaskEventType.CANCELLED,
                            occurred_at=moment,
                            payload={"reason": "delivery target closed"},
                        )
                        for delivery_id, tenant_id in cancelled
                    ],
                )
        logger.info("delivery_cancelled_closed count=%d", len(cancelled))
        return len(cancelled)

    # ------------------------------------------------------------------ 预留

    async def _reserve_candidate(
        self,
        moment: datetime,
        *,
        tenant_id: str,
        task_settings: TaskSettings,
    ) -> _Reserved | None:
        """先持久预留本次尝试再发送：多副本并发时只有一个能拿到候选。

        `attempt_count`、租约与 attempt 行在调用网关之前提交，因此进程崩溃或重启后
        退避进度与尝试上限都不会丢；重复请求在退避窗口内不会被再次选中。租约到期后
        另一个副本可以接手（下一次尝试），旧租约的确认会被 CAS 拒绝。
        """
        lease_until = moment + timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC)
        candidate_id = self._candidate_id(moment, tenant_id=tenant_id, task_settings=task_settings)
        async with self._session_factory() as session:
            async with session.begin():
                return await self._reserve(session, candidate_id, lease_until=lease_until, now=moment)

    def _candidate_id(
        self, moment: datetime, *, tenant_id: str, task_settings: TaskSettings
    ) -> ColumnElement[uuid.UUID]:
        """可投递候选：仍在预算内、已过退避窗口、当前无有效租约。"""
        backoff = sa.func.make_interval(
            0,
            0,
            0,
            0,
            0,
            0,
            task_settings.delivery_backoff_base_sec * sa.func.power(2, TaskDelivery.attempt_count),
        )
        return (
            select(TaskDelivery.id)
            .where(
                *self._detectable_conditions(),
                TaskDelivery.tenant_id == tenant_id,
                TaskDelivery.attempt_count < task_settings.delivery_max_attempts,
                or_(
                    TaskDelivery.attempt_count == 0,
                    TaskDelivery.update_time < moment - backoff,
                ),
                or_(TaskDelivery.lease_until.is_(None), TaskDelivery.lease_until < moment),
            )
            .order_by(TaskDelivery.create_time.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )

    async def _reserve(
        self,
        session: AsyncSession,
        candidate_id: ColumnElement[uuid.UUID],
        *,
        lease_until: datetime,
        now: datetime,
    ) -> _Reserved | None:
        """自增尝试、占租约并写 attempt 行（由调用方在同一事务内提交）。"""
        reserved = (
            await session.execute(
                update(TaskDelivery)
                .where(TaskDelivery.id == candidate_id)
                .values(
                    attempt_count=TaskDelivery.attempt_count + 1,
                    lease_owner=self._owner,
                    lease_until=lease_until,
                    update_time=now,
                )
                .returning(
                    TaskDelivery.id,
                    TaskDelivery.tenant_id,
                    TaskDelivery.execution_id,
                    TaskDelivery.delivery_route_id,
                    TaskDelivery.delivery_kind,
                    TaskDelivery.delivery_key,
                    TaskDelivery.attempt_count,
                )
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
        if reserved is None:
            return None
        _start_attempt(session, reserved, now=now)
        return _reserved_from_row(reserved, lease_owner=self._owner, lease_until=lease_until)

    def _detectable_conditions(self) -> tuple[ColumnElement[bool], ...]:
        """设置无关的「这条记录还想被投递」谓词（退避/上限判断留到取到快照后）。

        - 最终结果：只有 Execution 已终态才可发（立即任务在创建时就建了行）；
        - Human 问题：只有对应 interaction 仍 OPEN 才可发；
        - 网页投递额外要求结果发件已出结论：受理前不消耗投递尝试。

        「站内入口」集合按本进程平台配置动态合成（内建 Web 面 ∪ 外部平台通道），新增平台
        无需改这里。
        """
        local_stream_channels = web_surface_channels_from_settings(self._settings)
        final_ready = sa.and_(
            TaskDelivery.delivery_kind == str(DeliveryKind.PUSH),
            _live_execution_exists(),
            select(TaskExecution.id)
            .where(
                TaskExecution.id == TaskDelivery.execution_id,
                TaskExecution.tenant_id == TaskDelivery.tenant_id,
                TaskExecution.status.in_(TERMINAL_STATUSES),
            )
            .exists(),
            _webchat_result_settled(local_stream_channels),
        )
        question_ready = sa.and_(
            TaskDelivery.delivery_kind == str(DeliveryKind.HUMAN_QUESTION),
            _open_interaction_exists(),
        )
        return (
            TaskDelivery.status == PENDING,
            TaskDelivery.is_deleted.is_(False),
            or_(final_ready, question_ready),
        )

    # ------------------------------------------------------------------ 发送

    async def _attempt_delivery(
        self,
        reserved: _Reserved,
        *,
        platform: PlatformSettings,
        now: datetime,
    ) -> DeliveryOutcome:
        max_attempts = platform.task.delivery_max_attempts
        increment(DELIVERY_ATTEMPT_METRIC)
        context = await self._load_context(reserved)
        if context is None:
            return await self._fail(
                reserved,
                failure=_Failure(CODE_EXECUTION_MISSING, "delivery target no longer exists", False),
                now=now,
                max_attempts=max_attempts,
            )
        route = context.route
        if route is None or route.status != ACTIVE_ROUTE:
            reason = "deleted" if route is None else f"status {route.status}"
            return await self._fail(
                reserved,
                failure=_Failure(
                    CODE_ROUTE_UNAVAILABLE, f"delivery route is {reason}", False
                ),
                now=now,
                max_attempts=max_attempts,
            )
        locale = platform.locale.default_locale
        if reserved.delivery_kind == str(DeliveryKind.HUMAN_QUESTION):
            prepared = self._question_request(reserved, context, route, locale)
        elif is_local_stream_channel(
            route.channel, web_surface_channels_from_settings(self._settings)
        ):
            # 站内入口（内建 Web 面 ∪ 外部平台通道）没有可推送的外部目标：送达 = Runtime
            # 持久用户收件箱提交（TASK-068）。外部平台也是站内入口，判错会让它们的定时
            # 结果走外部推送路径而失败。
            return await self._attempt_inbox(reserved, context, now=now, max_attempts=max_attempts)
        else:
            prepared = await self._final_request(reserved, context, route, locale)
        if isinstance(prepared, _Failure):
            return await self._fail(
                reserved,
                failure=prepared,
                now=now,
                max_attempts=max_attempts,
            )
        return await self._send_and_interpret(
            reserved, prepared, now=now, max_attempts=max_attempts
        )

    async def _load_context(self, reserved: _Reserved) -> _DeliveryContext | None:
        interaction_id = interaction_id_from_key(reserved.delivery_key)
        async with self._session_factory() as session:
            execution = await session.scalar(
                select(TaskExecution).where(
                    TaskExecution.id == reserved.execution_id,
                    TaskExecution.tenant_id == reserved.tenant_id,
                    TaskExecution.is_deleted.is_(False),
                )
            )
            if execution is None:
                return None
            route = await session.scalar(
                select(DeliveryRoute).where(
                    DeliveryRoute.id == reserved.delivery_route_id,
                    DeliveryRoute.tenant_id == reserved.tenant_id,
                    DeliveryRoute.is_deleted.is_(False),
                )
            )
            schedule_name = None
            if execution.schedule_id is not None:
                schedule_name = await session.scalar(
                    select(TaskSchedule.name).where(
                        TaskSchedule.id == execution.schedule_id,
                        TaskSchedule.tenant_id == reserved.tenant_id,
                        TaskSchedule.is_deleted.is_(False),
                    )
                )
            interaction = None
            if interaction_id is not None:
                interaction = await session.scalar(
                    select(TaskHumanInteraction).where(
                        TaskHumanInteraction.id == interaction_id,
                        TaskHumanInteraction.tenant_id == reserved.tenant_id,
                        TaskHumanInteraction.is_deleted.is_(False),
                    )
                )
        return _DeliveryContext(
            execution=execution, route=route, schedule_name=schedule_name, interaction=interaction
        )

    def _question_request(
        self,
        reserved: _Reserved,
        context: _DeliveryContext,
        route: DeliveryRoute,
        locale: str,
    ) -> DeliveryRequest | _Failure:
        interaction = context.interaction
        if interaction is None:
            return _Failure(CODE_INTERACTION_MISSING, "human interaction no longer exists", False)
        if interaction.status != OPEN:
            return _Failure(
                CODE_INTERACTION_CLOSED, f"human interaction is {interaction.status}", False
            )
        if route.channel not in OUTBOUND_CHANNELS:
            return _Failure(
                CODE_UNSUPPORTED_CHANNEL,
                f"channel {route.channel} cannot push a human question",
                False,
            )
        question_title = task_title(context.execution, context.schedule_name, locale)
        return DeliveryRequest(
            tenant_id=reserved.tenant_id,
            task_id=context.execution.id,
            delivery_key=reserved.delivery_key,
            route=_route_input(route),
            message=build_human_question_message(
                question=interaction.question,
                title=question_title,
                deadline_at=interaction.deadline_at,
                locale=locale,
            ),
            agent_id=context.execution.agent_id,
            platform_user_id=route.platform_user_id,
        )

    async def _final_request(
        self,
        reserved: _Reserved,
        context: _DeliveryContext,
        route: DeliveryRoute,
        locale: str,
    ) -> DeliveryRequest | _Failure:
        execution = context.execution
        try:
            artifact = await self._resolve_artifact(execution)
        except ArtifactResolveError as exc:
            return _Failure("DELIVERY_ARTIFACT_UNRESOLVED", f"artifact resolve: {exc}", True)
        return DeliveryRequest(
            tenant_id=reserved.tenant_id,
            task_id=execution.id,
            delivery_key=reserved.delivery_key,
            route=_route_input(route),
            message=build_delivery_message(
                execution,
                locale,
                title=task_title(execution, context.schedule_name, locale),
                artifact=artifact,
            ),
            artifact_ids=[execution.result_artifact_id] if execution.result_artifact_id else [],
            agent_id=execution.agent_id,
            platform_user_id=route.platform_user_id,
        )

    async def _resolve_artifact(self, task: TaskExecution) -> AttachmentRef | None:
        """把 `result_artifact_id` 解析成渠道中立的引用。

        **两类失败分开对待**（这是本方法存在的全部理由）：
        - 产物**没了**（404）⇒ 重试多少次都不会好 ⇒ 退回**文本形态**（含任务结论），
          而不是让这条投递永远卡住、也不是给用户一串死 ID；
        - **没拿到结论**（超时/5xx）⇒ 可重试，**不**降级成文本：降级会把"该发文件却发了
          串 ID"**固化**下来，而那正是本需求要消灭的东西。
        """
        if task.result_artifact_id is None or self._resolver is None:
            return None
        try:
            return await self._resolver.resolve(task.result_artifact_id, tenant_id=task.tenant_id)
        except ArtifactGoneError:
            increment(DELIVERY_ARTIFACT_GONE_METRIC)
            logger.warning("delivery_artifact_gone task_id=%s", task.id)
            return None

    async def _send_and_interpret(
        self,
        reserved: _Reserved,
        request: DeliveryRequest,
        *,
        now: datetime,
        max_attempts: int,
    ) -> DeliveryOutcome:
        try:
            result = await self._client.deliver(request)
        except DeliveryTransportError as exc:
            return await self._fail(
                reserved,
                failure=_Failure(CODE_TRANSPORT, str(exc), True),
                now=now,
                max_attempts=max_attempts,
            )
        return await self._interpret_result(
            reserved, result, now=now, max_attempts=max_attempts
        )

    async def _interpret_result(
        self,
        reserved: _Reserved,
        result: DeliveryResult,
        *,
        now: datetime,
        max_attempts: int,
    ) -> DeliveryOutcome:
        status_code = result.status_code
        if 200 <= status_code < 300 and result.delivered:
            return await self._mark_sent(
                reserved, response_ref=str(status_code), now=now, http_status=status_code
            )
        if 200 <= status_code < 300:
            # Gateway 只占位/未确认送达：不得置 SENT，按可重试失败退避后重试。
            failure = _Failure(
                result.error_code or CODE_UNCONFIRMED,
                "delivery accepted as in-flight placeholder, not delivered yet",
                True,
            )
        else:
            failure = _Failure(
                result.error_code or f"HTTP_{status_code}",
                f"delivery returned status {status_code}",
                not (400 <= status_code < 500),
            )
        return await self._fail(
            reserved,
            failure=failure,
            now=now,
            max_attempts=max_attempts,
            http_status=status_code,
        )

    # ---------------------------------------------------------------- 网页收件箱路径

    async def _attempt_inbox(
        self,
        reserved: _Reserved,
        context: _DeliveryContext,
        *,
        now: datetime,
        max_attempts: int,
    ) -> DeliveryOutcome:
        """WEBCHAT 送达 = Runtime 持久用户收件箱提交（TASK-068；design §3.5/§3.6）。

        三条纪律：先持久后 ack（只有 Runtime 回 `persisted=true` 才置 SENT）；按原来源定位
        （会话/入口由 Runtime 从真实行导出，不编造）；重投命中（交付键稳定，重投不新增未读）。
        """
        execution = context.execution
        run_id = execution.source_run_id
        blocked = await self._inbox_blocker(execution)
        if blocked is not None:
            return await self._fail(
                reserved, failure=blocked, now=now, max_attempts=max_attempts
            )
        assert run_id is not None, "WEBCHAT 任务结果必须有来源 Run"
        assert self._webchat_inbox is not None, "WEBCHAT 投递必须装配收件箱端口"
        registration = WebchatTaskResultRegistration(
            task_id=execution.id,
            run_id=run_id,
            user_id=execution.actor_user_id,
            artifact_ids=[execution.result_artifact_id] if execution.result_artifact_id else [],
        )
        try:
            receipt = await self._webchat_inbox.register_task_result(
                tenant_id=execution.tenant_id, registration=registration
            )
        except DeliveryTransportError as exc:
            return await self._fail(
                reserved,
                failure=_Failure(CODE_TRANSPORT, str(exc), True),
                now=now,
                max_attempts=max_attempts,
            )
        return await self._interpret_inbox(
            reserved, receipt, now=now, max_attempts=max_attempts
        )

    async def _inbox_blocker(self, execution: TaskExecution) -> _Failure | None:
        """入箱前置：受信端口与来源 Run 必须都在，且结果发件已出结论。"""
        if self._webchat_inbox is None or execution.source_run_id is None:
            return _Failure(
                CODE_INBOX_UNAVAILABLE,
                "webchat delivery requires the trusted inbox port and the source run",
                False,
            )
        publication = await self._result_publication(execution)
        if publication is ControlOutboxStatus.SENT:
            return None
        return _Failure(
            "WEBCHAT_RESULT_NOT_PUBLISHED",
            f"background result publication is {publication or 'missing'}",
            publication is ControlOutboxStatus.PENDING,
        )

    async def _interpret_inbox(
        self,
        reserved: _Reserved,
        receipt: WebchatInboxReceipt,
        *,
        now: datetime,
        max_attempts: int,
    ) -> DeliveryOutcome:
        """入箱回执裁决：`persisted=true` 才送达；4xx 是确定性失败，其余可重试。"""
        if 200 <= receipt.status_code < 300 and receipt.persisted:
            return await self._mark_sent(
                reserved, response_ref=str(receipt.status_code), now=now, http_status=receipt.status_code
            )
        error = f"webchat inbox did not confirm persistence ({receipt.status_code})"
        if receipt.error_code:
            error = f"{error}: {receipt.error_code}"
        return await self._fail(
            reserved,
            failure=_Failure(
                receipt.error_code or CODE_UNCONFIRMED,
                error,
                not (400 <= receipt.status_code < 500),
            ),
            now=now,
            max_attempts=max_attempts,
            http_status=receipt.status_code,
        )

    async def _result_publication(self, task: TaskExecution) -> ControlOutboxStatus | None:
        """该 Task 终态结果的 Runtime 发件状态（`None` = 从未发布）。"""
        async with self._session_factory() as session:
            status = await session.scalar(
                select(RuntimeResultOutbox.status)
                .where(
                    RuntimeResultOutbox.tenant_id == task.tenant_id,
                    RuntimeResultOutbox.task_id == task.id,
                    RuntimeResultOutbox.is_deleted.is_(False),
                )
                .order_by(RuntimeResultOutbox.create_time.desc())
                .limit(1)
            )
        return None if status is None else ControlOutboxStatus(status)

    # ---------------------------------------------------------------- 结果落库（CAS）

    async def _mark_sent(
        self,
        reserved: _Reserved,
        *,
        response_ref: str | None,
        now: datetime,
        http_status: int,
    ) -> DeliveryOutcome:
        """确认送达：CAS 只在**本次租约**仍有效时生效，旧租约不得确认。"""
        async with self._session_factory() as session:
            async with session.begin():
                confirmed = await self._confirm_sent(
                    session, reserved, now=now, response_ref=response_ref
                )
        if not confirmed:
            # 租约已被别人接手：东西可能发出去了，但**本次尝试**不能再宣称送达。
            logger.warning(
                "delivery_lease_lost delivery_id=%s owner=%s", reserved.delivery_id, self._owner
            )
            return DeliveryOutcome(
                delivery_id=reserved.delivery_id,
                execution_id=reserved.execution_id,
                http_status=http_status,
                error="delivery lease was taken over before confirmation",
                sent=False,
            )
        record_outcome(DELIVERY_METRIC, SENT)
        return DeliveryOutcome(
            delivery_id=reserved.delivery_id,
            execution_id=reserved.execution_id,
            http_status=http_status,
            error=None,
            sent=True,
        )

    async def _confirm_sent(
        self,
        session: AsyncSession,
        reserved: _Reserved,
        *,
        now: datetime,
        response_ref: str | None,
    ) -> bool:
        """本次租约仍未失效才写 SENT：失败即代表旧租约被接管。"""
        result = await session.execute(
            update(TaskDelivery)
            .where(*self._lease_conditions(reserved))
            .values(
                status=SENT,
                sent_at=now,
                last_error_code=None,
                last_error_message=None,
                lease_owner=None,
                lease_until=None,
                update_time=now,
            )
            .execution_options(synchronize_session=False)
        )
        if int(cast(CursorResult[Any], result).rowcount) != 1:
            return False
        await self._finish_attempt(
            session,
            delivery_id=reserved.delivery_id,
            tenant_id=reserved.tenant_id,
            attempt_no=reserved.attempt_no,
            status=SENT,
            now=now,
            response_ref=response_ref,
        )
        await append_event(
            session,
            tenant_id=reserved.tenant_id,
            entity_type=EntityType.DELIVERY,
            entity_id=reserved.delivery_id,
            event_type=TaskEventType.SENT,
            occurred_at=now,
            payload={"delivery_key": reserved.delivery_key, "attempt": reserved.attempt_no},
        )
        return True

    async def _fail(
        self,
        reserved: _Reserved,
        *,
        failure: _Failure,
        now: datetime,
        max_attempts: int,
        http_status: int | None = None,
    ) -> DeliveryOutcome:
        """落一次失败：还有预算就留在 PENDING 退避重试，耗尽或确定性失败才终态。"""
        terminal = not failure.retryable or reserved.attempt_no >= max_attempts
        async with self._session_factory() as session:
            async with session.begin():
                applied = await self._apply_failure(
                    session, reserved, failure=failure, terminal=terminal, now=now
                )
        if not applied:
            logger.warning(
                "delivery_lease_lost delivery_id=%s owner=%s", reserved.delivery_id, self._owner
            )
            return DeliveryOutcome(
                delivery_id=reserved.delivery_id,
                execution_id=reserved.execution_id,
                http_status=http_status,
                error="delivery lease was taken over before confirmation",
                sent=False,
            )
        if terminal:
            increment(DELIVERY_FAILED_METRIC)
        record_outcome(DELIVERY_METRIC, FAILED if terminal else PENDING)
        return DeliveryOutcome(
            delivery_id=reserved.delivery_id,
            execution_id=reserved.execution_id,
            http_status=http_status,
            error=failure.message,
            sent=False,
        )

    async def _apply_failure(
        self,
        session: AsyncSession,
        reserved: _Reserved,
        *,
        failure: _Failure,
        terminal: bool,
        now: datetime,
    ) -> bool:
        """把失败写进投递行与 attempt 行（CAS 在本次租约上）；返回是否由本次尝试落库。"""
        result = await session.execute(
            update(TaskDelivery)
            .where(*self._lease_conditions(reserved))
            .values(
                status=FAILED if terminal else PENDING,
                last_error_code=failure.code[:64],
                last_error_message=failure.message[:2000],
                lease_owner=None,
                lease_until=None,
                update_time=now,
            )
            .execution_options(synchronize_session=False)
        )
        if int(cast(CursorResult[Any], result).rowcount) != 1:
            return False
        await self._finish_attempt(
            session,
            delivery_id=reserved.delivery_id,
            tenant_id=reserved.tenant_id,
            attempt_no=reserved.attempt_no,
            status=FAILED,
            now=now,
            code=failure.code,
        )
        if terminal:
            await self._record_terminal_failure(session, reserved, failure=failure, now=now)
        return True

    async def _record_terminal_failure(
        self,
        session: AsyncSession,
        reserved: _Reserved,
        *,
        failure: _Failure,
        now: datetime,
    ) -> None:
        """终态失败的审计事件；路由永久失效时同时把来源 Schedule 置为待处理。"""
        await append_event(
            session,
            tenant_id=reserved.tenant_id,
            entity_type=EntityType.DELIVERY,
            entity_id=reserved.delivery_id,
            event_type=TaskEventType.FAILED,
            occurred_at=now,
            payload={
                "delivery_key": reserved.delivery_key,
                "error_code": failure.code,
                "attempt": reserved.attempt_no,
                "terminal": True,
            },
        )
        if failure.code in ROUTE_UNAVAILABLE_CODES:
            await self._flag_schedule_attention(
                session,
                tenant_id=reserved.tenant_id,
                execution_id=reserved.execution_id,
                code=failure.code,
                now=now,
            )

    async def _flag_schedule_attention(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        execution_id: uuid.UUID,
        code: str,
        now: datetime,
    ) -> None:
        """路由永久失效：来源 Schedule 置 `NEEDS_ATTENTION`（与 outbox 消费者同一写入点）。"""
        await flag_schedule_attention(
            session,
            tenant_id=tenant_id,
            execution_id=execution_id,
            code=code,
            now=now,
        )

    @staticmethod
    def _lease_conditions(reserved: _Reserved) -> tuple[ColumnElement[bool], ...]:
        """本次尝试的栅栏：状态、所有者与租约截止都必须仍是预留时的样子。"""
        return (
            TaskDelivery.id == reserved.delivery_id,
            TaskDelivery.status == PENDING,
            TaskDelivery.is_deleted.is_(False),
            TaskDelivery.lease_owner == reserved.lease_owner,
            TaskDelivery.lease_until == reserved.lease_until,
        )

    @staticmethod
    async def _finish_attempt(
        session: AsyncSession,
        *,
        delivery_id: uuid.UUID,
        tenant_id: str,
        attempt_no: int,
        status: str,
        now: datetime,
        code: str | None = None,
        response_ref: str | None = None,
    ) -> None:
        await session.execute(
            update(TaskDeliveryAttempt)
            .where(
                TaskDeliveryAttempt.delivery_id == delivery_id,
                TaskDeliveryAttempt.tenant_id == tenant_id,
                TaskDeliveryAttempt.attempt_no == attempt_no,
            )
            .values(
                status=status,
                error_code=None if code is None else code[:64],
                response_ref=response_ref,
                finished_at=now,
                update_time=now,
            )
            .execution_options(synchronize_session=False)
        )


def _start_attempt(session: AsyncSession, row: Any, *, now: datetime) -> None:
    """本次尝试的持久行：发送前提交，崩溃后仍可从 attempt 行追溯。"""
    delivery_id, tenant_id, _execution_id, _route_id, _kind, key, attempt_no = row
    session.add(
        TaskDeliveryAttempt(
            tenant_id=tenant_id,
            delivery_id=delivery_id,
            attempt_no=attempt_no,
            request_ref=key,
            status=PENDING,
            started_at=now,
        )
    )


def _reserved_from_row(
    row: Any, *, lease_owner: str, lease_until: datetime
) -> _Reserved:
    delivery_id, tenant_id, execution_id, route_id, kind, key, attempt_no = row
    return _Reserved(
        delivery_id=delivery_id,
        tenant_id=tenant_id,
        execution_id=execution_id,
        delivery_route_id=route_id,
        delivery_kind=kind,
        delivery_key=key,
        attempt_no=attempt_no,
        lease_owner=lease_owner,
        lease_until=lease_until,
    )


def _dead_delivery_targets() -> ColumnElement[uuid.UUID]:
    """已经不可能送达的投递：问题已关闭、执行已被清理（有界，按创建先后）。"""
    closed_question = sa.and_(
        TaskDelivery.delivery_kind == str(DeliveryKind.HUMAN_QUESTION),
        ~_open_interaction_exists(),
    )
    dead_execution = sa.and_(
        TaskDelivery.delivery_kind == str(DeliveryKind.PUSH),
        ~_live_execution_exists(),
    )
    return (
        select(TaskDelivery.id)
        .where(
            TaskDelivery.status == PENDING,
            TaskDelivery.is_deleted.is_(False),
            or_(closed_question, dead_execution),
        )
        .order_by(TaskDelivery.create_time.asc())
        .limit(MAX_DEAD_CANCELLATIONS_PER_TICK)
        .scalar_subquery()
    )


def _pending_question_query() -> Any:
    """待推送的 OPEN 问题：PUSH 来源 + 外发通道 + 还没有同键投递。"""
    key = sa.func.concat(HUMAN_KEY_PREFIX, sa.cast(TaskHumanInteraction.id, sa.String))
    no_delivery = ~(
        select(TaskDelivery.id)
        .where(
            TaskDelivery.tenant_id == TaskHumanInteraction.tenant_id,
            TaskDelivery.delivery_key == key,
            TaskDelivery.is_deleted.is_(False),
        )
        .exists()
    )
    return (
        select(
            TaskHumanInteraction.id,
            TaskHumanInteraction.tenant_id,
            TaskHumanInteraction.execution_id,
            TaskScheduleRevision.delivery_route_id,
        )
        .join(
            TaskExecution,
            sa.and_(
                TaskExecution.id == TaskHumanInteraction.execution_id,
                TaskExecution.tenant_id == TaskHumanInteraction.tenant_id,
            ),
        )
        .join(
            TaskScheduleRevision,
            sa.and_(
                TaskScheduleRevision.id == TaskExecution.schedule_revision_id,
                TaskScheduleRevision.is_deleted.is_(False),
            ),
        )
        .join(DeliveryRoute, DeliveryRoute.id == TaskScheduleRevision.delivery_route_id)
        .where(
            TaskHumanInteraction.status == OPEN,
            TaskHumanInteraction.is_deleted.is_(False),
            TaskExecution.is_deleted.is_(False),
            TaskScheduleRevision.delivery_intent == PUSH_INTENT,
            DeliveryRoute.is_deleted.is_(False),
            DeliveryRoute.channel.in_(sorted(OUTBOUND_CHANNELS)),
            no_delivery,
        )
        .order_by(TaskHumanInteraction.create_time.asc())
        .limit(MAX_QUESTION_ENQUEUES_PER_TICK)
    )


async def _insert_question_deliveries(
    session: AsyncSession, rows: Any, moment: datetime
) -> list[tuple[uuid.UUID, str]]:
    """按发现结果补记投递行并记 `DELIVERY/ENQUEUED`；同键冲突（并发另一副本）不算失败。"""
    inserted = (
        await session.execute(
            pg_insert(TaskDelivery)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "execution_id": execution_id,
                        "delivery_route_id": route_id,
                        "delivery_kind": str(DeliveryKind.HUMAN_QUESTION),
                        "delivery_key": human_delivery_key(interaction_id),
                        "status": PENDING,
                        "attempt_count": 0,
                    }
                    for interaction_id, tenant_id, execution_id, route_id in rows
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[TaskDelivery.tenant_id, TaskDelivery.delivery_key],
                index_where=TaskDelivery.is_deleted == false(),
            )
            .returning(TaskDelivery.id, TaskDelivery.tenant_id)
        )
    ).all()
    pairs = [(row[0], row[1]) for row in inserted]
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=tenant_id,
                entity_type=EntityType.DELIVERY,
                entity_id=delivery_id,
                event_type=TaskEventType.ENQUEUED,
                occurred_at=moment,
                payload={"kind": str(DeliveryKind.HUMAN_QUESTION)},
            )
            for delivery_id, tenant_id in pairs
        ],
    )
    return pairs


def _live_execution_exists() -> ColumnElement[bool]:
    return (
        select(TaskExecution.id)
        .where(
            TaskExecution.id == TaskDelivery.execution_id,
            TaskExecution.tenant_id == TaskDelivery.tenant_id,
            TaskExecution.is_deleted.is_(False),
        )
        .exists()
    )


def _open_interaction_exists() -> ColumnElement[bool]:
    """投递行对应的那个 interaction（键里带 id）仍然 OPEN。"""
    return (
        select(TaskHumanInteraction.id)
        .where(
            TaskHumanInteraction.tenant_id == TaskDelivery.tenant_id,
            TaskHumanInteraction.is_deleted.is_(False),
            TaskHumanInteraction.status == OPEN,
            TaskHumanInteraction.execution_id == TaskDelivery.execution_id,
            TaskDelivery.delivery_key
            == sa.func.concat(HUMAN_KEY_PREFIX, sa.cast(TaskHumanInteraction.id, sa.String)),
        )
        .exists()
    )


def _webchat_result_settled(
    local_stream_channels: Iterable[str],
) -> ColumnElement[bool]:
    """网页投递的前置条件：该 Task 结果的 Runtime 发件已出结论（TASK-068）。

    网页"送达"是提交进 Runtime 的持久用户收件箱，而 Runtime 只接受已被受理的结果。结果
    发件还在 `PENDING` 时不得消耗投递尝试——否则发件还在退避，投递先把预算烧光，用户
    永远看不到结果（design §3.6「Worker 结果沿任务完成后的持久投递意图重试」）。

    `local_stream_channels` 是站内入口集合（内建 Web 面 ∪ 已启用的外部平台通道），由调用方
    按平台配置算出——外部平台也是站内入口，漏掉会让它们的定时结果永远发不出去。
    """
    local_stream_route = (
        select(DeliveryRoute.id)
        .where(
            DeliveryRoute.id == TaskDelivery.delivery_route_id,
            DeliveryRoute.channel.in_(sorted(local_stream_channels)),
        )
        .exists()
    )
    publishing = (
        select(RuntimeResultOutbox.id)
        .where(
            RuntimeResultOutbox.task_id == TaskDelivery.execution_id,
            RuntimeResultOutbox.is_deleted.is_(False),
            RuntimeResultOutbox.status == ControlOutboxStatus.PENDING,
        )
        .exists()
    )
    return or_(
        sa.not_(local_stream_route),
        sa.not_(
            select(TaskExecution.id)
            .where(
                TaskExecution.id == TaskDelivery.execution_id,
                TaskExecution.source_run_id.is_not(None),
            )
            .exists()
        ),
        sa.not_(publishing),
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


__all__ = ["DELIVERY_RESERVATION_LEASE_SEC", "MAX_TENANTS_PER_TICK", "DeliveryLoop", "DeliveryOutcome"]
