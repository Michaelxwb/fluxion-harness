"""Runtime 独立可靠交付泵（design §3.2/§3.6/§4.1；TASK-066）。

**它解决什么**：机器人终态交付不能依赖 Gateway 内存里的 SSE 订阅/ReplySession——Gateway 重启、
排队超过机器人回复窗口或旧回调失效时，用户就再也收不到结果。Runtime 终态与 `PENDING` 出站意图
在**同一事务**提交（TASK-063），本泵在 Runtime 进程内按租约把**已持久**的意图交付出去。

**边界与不变量**：

- **独立于请求/执行路径**：不占执行槽、不受请求并发影响，也不排在长流队尾——它只按
  `channel_delivery_*` 启动预算（并发/timeout/租约/poll/batch）自己跑，异常只影响本轮
  （`run_forever` 记日志后继续），不拖累别的循环（design §3.2）。
- **领取有界**：`claim_due` 只认 `PENDING` + `next_attempt_at` 到期 + `attempt` 未超预算的行，
  谓词与 `ix_channel_notification_due`（partial：`delivery_state='PENDING'`）同形，按
  `(tenant_id, next_attempt_at)` 有序取；**领取数量 ≤ 空闲投递槽，无槽不领取**（§3.7）。
  领取本身不加锁：状态转换由 TASK-065 的 `reserve` 行锁内完成，多副本竞争由 epoch 栅栏裁决。
- **只重试已持久结果**：`reserve → 发送 → ack` 全走 TASK-065 的同一 PG 状态转换
  （API-18 的线协议就是它），本泵不重新执行任何外部写任务。
- **失约即停手**：`renew` 一旦拿不到租约（被接管/过期）就取消本地发送且**不 ack**——迟到结果
  没有位置；epoch 防重保证被接管的旧 attempt 改不动行。
- **诚实结局**：`DELIVERED` 只在**实际发送成功**后写；确定没发出去 ⇒ `NOT_SENT`（回 PENDING，
  按平台设置的退避基数重投）；没拿到结论 ⇒ `UNKNOWN`（保留核实，不自动重发，不标已送达）。
- **重试预算是进程级的**：发送侧的瞬时故障（设置源不可达/DB 抖动/发送器异常）按
  `retry_base_sec·2^(k-1)`（上限 `retry_cap_sec`）有界重试，最多 `max_attempts` 轮；同一
  `attempt_id` 重复 reserve 命中同一租约，不虚增 `attempt` 计数。
- **时间**：到期、租约、退避一律用 PG 服务端时钟（`database_now`）；事务内零网络请求。
- **不新增部署单元**：本泵在 Runtime 进程内由装配根挂起（`main.lifespan`），交付仍经既有
  Gateway `/internal/deliveries` 适配器。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Protocol

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import DeliveryMessage, DeliveryRequest, DeliveryResponse, DeliveryRouteInput
from muad_contracts.channel_delivery import (
    DELIVERY_LEASE_MAX_SEC,
    DELIVERY_LEASE_MIN_SEC,
    DeliveryAckOutcome,
)
from pydantic import JsonValue

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.db_clock import database_now
from ..infrastructure.gateway_delivery_client import (
    DeliveryNotSentError,
    DeliveryUnavailableError,
)
from ..infrastructure.models.runtime import CanonicalEvent, ChannelNotification, RunRecord
from ..metrics import (
    CHANNEL_DELIVERY_FAILURE_METRIC,
    CHANNEL_DELIVERY_METRIC,
    RUNTIME_MAINTENANCE_METRIC,
    record_outcome,
    record_reason,
)
from .channel_delivery import DeliveryConfirmation, DeliveryLease
from .channel_notifications import DELIVERY_PENDING
from .run_service import delivery_route_of

logger = logging.getLogger(__name__)

#: 交付正文的来源键（终态事件 `final_text`；待确认事件 `prompt`）：只读**已持久**的载荷。
_TEXT_KEYS = ("final_text", "prompt", "text")


@dataclass(frozen=True, slots=True)
class DeliveryPumpPolicy:
    """交付泵的每进程启动预算（来源 `SharedSettings`；重启生效，design §3.7）。"""

    concurrency: int
    timeout_sec: float
    lease_sec: int
    poll_sec: float
    batch_size: int
    retry_base_sec: float
    retry_cap_sec: float
    max_attempts: int

    def __post_init__(self) -> None:
        if self.concurrency < 1 or self.batch_size < 1 or self.max_attempts < 1:
            raise ValueError("channel delivery pump budgets must be positive")
        if self.timeout_sec <= 0 or self.poll_sec <= 0:
            raise ValueError("channel delivery pump timings must be positive")
        if not DELIVERY_LEASE_MIN_SEC <= self.lease_sec <= DELIVERY_LEASE_MAX_SEC:
            raise ValueError("channel delivery lease must stay inside the protocol bounds")
        if self.timeout_sec >= self.lease_sec:
            raise ValueError("channel delivery timeout must be shorter than the lease")
        if not 0 < self.retry_base_sec <= self.retry_cap_sec:
            raise ValueError("channel delivery retry backoff must satisfy 0 < base <= cap")

    @classmethod
    def from_settings(cls, settings: SharedSettings) -> DeliveryPumpPolicy:
        return cls(
            concurrency=settings.channel_delivery_concurrency,
            timeout_sec=settings.channel_delivery_timeout_sec,
            lease_sec=_lease_seconds(settings.channel_delivery_lease_sec),
            poll_sec=settings.channel_delivery_poll_sec,
            batch_size=settings.channel_delivery_batch_size,
            retry_base_sec=settings.channel_delivery_retry_base_sec,
            retry_cap_sec=settings.channel_delivery_retry_cap_sec,
            max_attempts=settings.channel_delivery_max_attempts,
        )


@dataclass(frozen=True, slots=True)
class DeliveryIntent:
    """一条到期出站意图（领取时的行事实 + Run 的入站路由 + 已持久正文）。"""

    notification_id: uuid.UUID
    tenant_id: str
    delivery_key: str
    kind: str
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    entry_id: uuid.UUID
    artifact_ids: tuple[uuid.UUID, ...]
    attempt: int
    route: DeliveryRouteInput | None
    text: str


@dataclass(frozen=True, slots=True)
class DeliverySendOutcome:
    """一次真实发送的结局：只有 `DELIVERED` 是送达；`result` 冻结首次结果（码/引用，不含正文）。"""

    outcome: DeliveryAckOutcome
    result: dict[str, JsonValue] | None = None


class DeliveryProtocolPort(Protocol):
    """TASK-065 的 reserve/renew/ack（`ChannelDeliveryService` 与 `TenantChannelDelivery` 都满足）。"""

    async def reserve(
        self,
        *,
        tenant_id: str,
        delivery_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> DeliveryLease: ...

    async def renew(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> DeliveryLease: ...

    async def ack(
        self,
        *,
        tenant_id: str,
        notification_id: uuid.UUID,
        delivery_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: DeliveryAckOutcome,
        result: Mapping[str, JsonValue] | None = None,
    ) -> DeliveryConfirmation: ...


class DeliveryIntentSender(Protocol):
    """把一条意图真正发出去（渠道细节留在实现里；本泵只认结局）。"""

    async def send(self, intent: DeliveryIntent) -> DeliverySendOutcome: ...


class DeliveryTransportPort(Protocol):
    """Runtime → Gateway 的交付客户端（`GatewayDeliveryClient` 满足）。"""

    async def deliver(self, request: DeliveryRequest, *, trace_id: str = "") -> DeliveryResponse: ...


def _lease_seconds(value: float) -> int:
    """启动预算里的租期是秒数（float）：必须是整秒，且落在线协议边界内（显式失败不截断）。"""
    if not value.is_integer():
        raise ValueError("channel delivery lease must be whole seconds")
    return int(value)


def due_intents_statement(*, now: datetime, max_attempts: int, limit: int) -> sa.Select[Any]:
    """到期意图的领取谓词：与 `ix_channel_notification_due` 同形（真实执行计划可核）。

    隐藏（软删）通知**不**被排除：投递回执与重试资格不因隐藏而消失（TASK-063/§3.6）。
    """
    return (
        sa.select(ChannelNotification, RunRecord.channel_json, CanonicalEvent.payload_json)
        .join(
            RunRecord,
            sa.and_(
                RunRecord.id == ChannelNotification.run_id,
                RunRecord.tenant_id == ChannelNotification.tenant_id,
            ),
        )
        .outerjoin(
            CanonicalEvent,
            sa.and_(
                CanonicalEvent.id == ChannelNotification.event_id,
                CanonicalEvent.tenant_id == ChannelNotification.tenant_id,
            ),
        )
        .where(
            ChannelNotification.delivery_state == DELIVERY_PENDING,
            ChannelNotification.next_attempt_at <= now,
            ChannelNotification.attempt < max_attempts,
        )
        .order_by(
            ChannelNotification.tenant_id,
            ChannelNotification.next_attempt_at,
            ChannelNotification.id,
        )
        .limit(limit)
    )


def _deliverable_text(payload: Mapping[str, Any] | None) -> str:
    """已持久事件载荷里的可交付正文；没有可交付正文时返回空串（不编造文案）。"""
    if not payload:
        return ""
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _intent(row: sa.Row[Any]) -> DeliveryIntent:
    notification, channel_json, payload = row
    return DeliveryIntent(
        notification_id=notification.id,
        tenant_id=notification.tenant_id,
        delivery_key=notification.delivery_key,
        kind=notification.kind,
        run_id=notification.run_id,
        conversation_id=notification.conversation_id,
        user_id=notification.user_id,
        entry_id=notification.entry_id,
        artifact_ids=tuple(uuid.UUID(str(value)) for value in (notification.artifact_ids or ())),
        attempt=int(notification.attempt),
        route=delivery_route_of(channel_json),
        text=_deliverable_text(payload),
    )


class ChannelDeliveryPump:
    """按启动预算自主领取/投递的独立泵；每个在飞投递是一个受监督的独立任务。"""

    def __init__(
        self,
        sessions: SessionFactoryProvider,
        protocol: DeliveryProtocolPort,
        sender: DeliveryIntentSender,
        policy: DeliveryPumpPolicy,
    ) -> None:
        self._sessions = sessions
        self._protocol = protocol
        self._sender = sender
        self._policy = policy
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    @property
    def policy(self) -> DeliveryPumpPolicy:
        return self._policy

    @property
    def inflight(self) -> int:
        return len(self._tasks)

    @property
    def free_slots(self) -> int:
        return max(self._policy.concurrency - len(self._tasks), 0)

    async def claim_due(self, *, limit: int) -> list[DeliveryIntent]:
        """领取到期意图（PG 服务端时钟裁决到期；不占租约，状态转换在 `reserve` 行锁内）。"""
        if limit < 1:
            return []
        async with self._sessions()() as session:
            now = await database_now(session)
            rows = (
                await session.execute(
                    due_intents_statement(
                        now=now, max_attempts=self._policy.max_attempts, limit=limit
                    )
                )
            ).all()
        return [_intent(row) for row in rows]

    async def run_once(self) -> int:
        """一轮：领取 ≤ min(batch, 空闲槽) 条并提交监督投递；返回本轮启动的投递数。"""
        limit = min(self._policy.batch_size, self.free_slots)
        started = 0
        for intent in await self.claim_due(limit=limit):
            if intent.notification_id in self._tasks:
                continue
            task = asyncio.create_task(
                self._deliver(intent), name=f"channel-delivery:{intent.notification_id}"
            )
            self._tasks[intent.notification_id] = task
            task.add_done_callback(partial(self._finished, intent.notification_id))
            started += 1
        return started

    async def run_forever(self) -> None:
        """独立循环：本轮失败只记日志并继续，不拖累其它循环（design §3.2）。"""
        while True:
            try:
                await self.run_once()
            except Exception:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "delivery"})
                record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "pump_failed")
                logger.exception("channel_delivery_pump_failed")
            else:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "delivery"})
            await asyncio.sleep(self._policy.poll_sec)

    async def drain(self) -> None:
        """等待在飞投递结束（观测/收尾用；不取消它们）。"""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def close(self) -> None:
        """收尾：取消并等待所有在飞投递（装配根退出时调用）。"""
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _finished(self, key: uuid.UUID, task: asyncio.Task[None]) -> None:
        """单条独立回收：成功/异常/取消都只释放自己，不触碰别的在飞投递。"""
        self._tasks.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "crashed")
            logger.error(
                "channel_delivery_crashed",
                extra={"notification_id": str(key), "error_type": type(error).__name__},
            )

    async def _deliver(self, intent: DeliveryIntent) -> None:
        """一条意图的完整投递：有界重试（进程级预算）+ 失约即停手。"""
        attempt_id = uuid.uuid4()
        for round_index in range(self._policy.max_attempts):
            try:
                await self._deliver_once(intent, attempt_id)
                return
            except AppError as exc:
                if exc.code in (
                    ErrorCode.DELIVERY_LEASE_LOST,
                    ErrorCode.COMMON_NOT_FOUND,
                    ErrorCode.COMMON_CONFLICT,
                ):
                    return
                record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "attempt_failed")
                logger.warning(
                    "channel_delivery_attempt_failed",
                    extra={"notification_id": str(intent.notification_id), "code": str(exc.code)},
                )
            except Exception:
                record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "attempt_failed")
                logger.exception(
                    "channel_delivery_attempt_failed",
                    extra={"notification_id": str(intent.notification_id)},
                )
            if round_index + 1 < self._policy.max_attempts:
                await asyncio.sleep(
                    min(self._policy.retry_cap_sec, self._policy.retry_base_sec * 2**round_index)
                )

    async def _deliver_once(self, intent: DeliveryIntent, attempt_id: uuid.UUID) -> None:
        """一次 reserve → 发送 → ack；慢发送期间独立续租，失约则本地停手且不 ack。"""
        lease = await self._protocol.reserve(
            tenant_id=intent.tenant_id,
            delivery_key=intent.delivery_key,
            attempt_id=attempt_id,
            lease_sec=self._policy.lease_sec,
        )
        if lease.lease_until is None:
            # 已送达重放 / UNKNOWN 保留：本次没有授出租约，不发送、不 ack（也不是"已送达"）。
            record_outcome(CHANNEL_DELIVERY_METRIC, "SKIPPED")
            return
        send = asyncio.create_task(self._send_within_budget(intent))
        keep = asyncio.create_task(self._keep_lease(intent, attempt_id, lease.lease_epoch))
        try:
            done, _ = await asyncio.wait({send, keep}, return_when=asyncio.FIRST_COMPLETED)
            if keep in done:
                send.cancel()
                await _settle(send)
                keep.result()  # 失约异常在此抛出（不吞、不 ack）
            outcome = await send
        finally:
            await _cancel(keep)
        await self._protocol.ack(
            tenant_id=intent.tenant_id,
            notification_id=intent.notification_id,
            delivery_key=intent.delivery_key,
            attempt_id=attempt_id,
            epoch=lease.lease_epoch,
            outcome=outcome.outcome,
            result=outcome.result,
        )
        # 诚实结局：DELIVERED/NOT_SENT/UNKNOWN 按**实际发送结果**计数，不互相冒充。
        record_outcome(CHANNEL_DELIVERY_METRIC, _delivery_status(outcome.outcome))

    async def _send_within_budget(self, intent: DeliveryIntent) -> DeliverySendOutcome:
        """发送预算（`channel_delivery_timeout_sec`）：超时只说明没拿到结论，不说明没发出去。"""
        try:
            return await asyncio.wait_for(
                self._sender.send(intent), timeout=self._policy.timeout_sec
            )
        except TimeoutError:
            return DeliverySendOutcome(outcome="UNKNOWN", result={"reason": "timeout"})

    async def _keep_lease(self, intent: DeliveryIntent, attempt_id: uuid.UUID, epoch: int) -> None:
        """慢发送期间的续租：拿不到租约（被接管/过期）即抛 `DELIVERY_LEASE_LOST`。"""
        period = max(self._policy.lease_sec / 3, 0.5)
        while True:
            await asyncio.sleep(period)
            await self._protocol.renew(
                tenant_id=intent.tenant_id,
                notification_id=intent.notification_id,
                delivery_key=intent.delivery_key,
                attempt_id=attempt_id,
                epoch=epoch,
                lease_sec=self._policy.lease_sec,
            )


class GatewayDeliverySender:
    """经既有 Gateway `/internal/deliveries` 主动交付（design §3.6）：真实发送 + 诚实分类。

    路由来自 Run 持久化的入站渠道（`channel_json`），正文来自通知引用的**已持久**事件载荷；
    两者缺失时明确 `NOT_SENT`（没发出去 ≠ 发成功了）。产物形态（Task 结果附件）的交付接线
    属 TASK-068/094：本发送器只交付文本形态，不伪造已送达。
    """

    def __init__(self, client: DeliveryTransportPort) -> None:
        self._client = client

    async def send(self, intent: DeliveryIntent) -> DeliverySendOutcome:
        text = intent.text.strip()
        if intent.route is None:
            return DeliverySendOutcome(outcome="NOT_SENT", result={"reason": "route_missing"})
        if not text:
            return DeliverySendOutcome(outcome="NOT_SENT", result={"reason": "text_missing"})
        request = DeliveryRequest(
            tenant_id=intent.tenant_id,
            delivery_key=intent.delivery_key,
            route=intent.route,
            message=DeliveryMessage(type="text", text=text),
            artifact_ids=list(intent.artifact_ids),
        )
        try:
            response = await self._client.deliver(request)
        except DeliveryNotSentError as exc:
            return DeliverySendOutcome(outcome="NOT_SENT", result={"reason": str(exc)})
        except DeliveryUnavailableError as exc:
            return DeliverySendOutcome(outcome="UNKNOWN", result={"reason": str(exc)})
        if not response.delivered:
            # 网关只占位、未确认送达：不得当成功（"有占位"不是"已送达"）。
            return DeliverySendOutcome(outcome="NOT_SENT", result={"reason": "gateway_placeholder"})
        return DeliverySendOutcome(
            outcome="DELIVERED",
            result={"delivered": True, "deduplicated": response.deduplicated},
        )


def _delivery_status(outcome: str) -> str:
    """交付结局 → 目录 label 的**封闭词表**：只有 DELIVERED/NOT_SENT/UNKNOWN 三种诚实结局。

    词表外的取值说明协议坏了：显式失败，不把未知结局当标签发出去（更不冒充"已送达"）。
    """
    if outcome == "DELIVERED":
        return "DELIVERED"
    if outcome == "NOT_SENT":
        return "NOT_SENT"
    if outcome == "UNKNOWN":
        return "UNKNOWN"
    raise ValueError(f"unexpected delivery outcome {outcome!r}")


async def _cancel(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    await _settle(task)


async def _settle(task: asyncio.Task[Any]) -> None:
    """吞掉取消/异常：调用方已按显式路径处理过结局，这里只保证任务收干净。"""
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return
