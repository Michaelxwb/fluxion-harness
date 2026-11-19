"""消息级投递泵：领取 `runtime.message_delivery` 到期意图并发往 Gateway（design §4.5/§5.6）。

边界：

- **唯一 Run push 发送者**：Run 终态/等待消息在终态事务里写 `message_delivery`，本泵按租约
  经既有 Gateway `/internal/deliveries` 发送；不再有第二条 channel push 泵。
- **到期领取**：只认 `PENDING` + `next_attempt_at <= now` + `attempt_count < max_attempts`；
  谓词与 `ix_message_delivery_due` 同形。领取本身不授租约，状态转换在 `reserve` 行锁内完成。
- **诚实结局**：`DELIVERED` 是直发成功；`DEGRADED` 是降级成功（带取件链接）；`NOT_SENT`
  回 `PENDING` 退避；没拿到结论 `UNKNOWN`（保留核实，不自动重发）；显式路由失效 `FAILED`。
- **失约即停手**：续租失败即取消本地发送且不 ack；旧 epoch 的回执由状态机拒绝。
- Task 侧 outbox → Runtime 的迁移是 TASK-004；本泵不读 `task_delivery`，也不写它。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.db_counters import db_stage_scope
from muad_contracts import (
    DeliveryMessage,
    DeliveryRequest,
    DeliveryResponse,
    DeliveryRouteInput,
)
from pydantic import JsonValue

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.db_clock import database_now
from ..infrastructure.gateway_delivery_client import (
    DeliveryNotSentError,
    DeliveryUnavailableError,
)
from ..infrastructure.models.runtime import MessageDelivery
from ..metrics import (
    CHANNEL_DELIVERY_FAILURE_METRIC,
    CHANNEL_DELIVERY_METRIC,
    RUNTIME_MAINTENANCE_METRIC,
    record_outcome,
    record_reason,
)
from .channel_delivery_pump import DeliveryPumpPolicy
from .message_delivery import (
    MESSAGE_DEGRADED,
    MESSAGE_DELIVERED,
    MESSAGE_FAILED,
    MESSAGE_PENDING,
    MESSAGE_UNKNOWN,
    MessageDeliveryConfirmation,
    MessageDeliveryLease,
    MessageDeliveryPayload,
    MessageDeliveryService,
    delivery_payload_of,
    delivery_route_of_row,
    message_delivery_due_statement,
)

logger = logging.getLogger(__name__)

#: 投递泵的单次结局（词表与 message_delivery 状态机一致，不含 NOT_SENT——它落回 PENDING）。
MessageDeliveryOutcome = Literal["DELIVERED", "DEGRADED", "UNKNOWN", "FAILED"]


@dataclass(frozen=True, slots=True)
class MessageDeliveryIntent:
    """一条到期消息意图（行事实 + 已冻结载荷）。"""

    delivery_id: uuid.UUID
    tenant_id: str
    message_key: str
    target_key: str
    kind: str
    owner_type: str
    owner_id: uuid.UUID
    route: DeliveryRouteInput | None
    payload: MessageDeliveryPayload
    attempt: int
    message_id: str | None
    agent_id: uuid.UUID | None
    platform_user_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class MessageSendOutcome:
    """一次真实发送的结局；`result` 冻结在 attempt 回执与消息回执里。"""

    outcome: str
    result: dict[str, JsonValue] | None = None


class MessageDeliveryProtocolPort(Protocol):
    async def reserve(
        self,
        *,
        tenant_id: str,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease: ...

    async def renew(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        lease_sec: int | None = None,
    ) -> MessageDeliveryLease: ...

    async def ack(
        self,
        *,
        tenant_id: str,
        delivery_id: uuid.UUID,
        message_key: str,
        target_key: str,
        attempt_id: uuid.UUID,
        epoch: int,
        outcome: str,
        result: Mapping[str, JsonValue] | None = None,
    ) -> MessageDeliveryConfirmation: ...


class MessageDeliverySenderPort(Protocol):
    async def send(
        self,
        intent: MessageDeliveryIntent,
        *,
        attempt_id: uuid.UUID,
        lease_epoch: int,
    ) -> MessageSendOutcome: ...


class MessageDeliveryTransportPort(Protocol):
    async def deliver(
        self, request: DeliveryRequest, *, trace_id: str = ""
    ) -> DeliveryResponse: ...


class MessageDeliveryPump:
    """按启动预算自主领取/投递的消息级泵。"""

    def __init__(
        self,
        sessions: SessionFactoryProvider,
        protocol: MessageDeliveryProtocolPort,
        sender: MessageDeliverySenderPort,
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

    async def claim_due(self, *, limit: int) -> list[MessageDeliveryIntent]:
        if limit < 1:
            return []
        async with self._sessions()() as session:
            now = await database_now(session)
            rows = (
                await session.execute(
                    message_delivery_due_statement(
                        now=now,
                        max_attempts=self._policy.max_attempts,
                        limit=limit,
                    )
                )
            ).scalars()
            return [_intent(row) for row in rows]

    async def run_once(self) -> int:
        limit = min(self._policy.batch_size, self.free_slots)
        started = 0
        for intent in await self.claim_due(limit=limit):
            if intent.delivery_id in self._tasks:
                continue
            task = asyncio.create_task(
                self._deliver(intent), name=f"message-delivery:{intent.delivery_id}"
            )
            self._tasks[intent.delivery_id] = task
            task.add_done_callback(partial(self._finished, intent.delivery_id))
            started += 1
        return started

    @db_stage_scope("bg:delivery")
    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "delivery"})
                record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "pump_failed")
                logger.exception("message_delivery_pump_failed")
            else:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "delivery"})
            await asyncio.sleep(self._policy.poll_sec)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _finished(self, key: uuid.UUID, task: asyncio.Task[None]) -> None:
        self._tasks.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "crashed")
            logger.error(
                "message_delivery_crashed",
                extra={"delivery_id": str(key), "error_type": type(error).__name__},
            )

    async def _deliver(self, intent: MessageDeliveryIntent) -> None:
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
                    "message_delivery_attempt_failed",
                    extra={"delivery_id": str(intent.delivery_id), "code": str(exc.code)},
                )
            except Exception:
                record_reason(CHANNEL_DELIVERY_FAILURE_METRIC, "attempt_failed")
                logger.exception(
                    "message_delivery_attempt_failed",
                    extra={"delivery_id": str(intent.delivery_id)},
                )
            if round_index + 1 < self._policy.max_attempts:
                await asyncio.sleep(
                    min(
                        self._policy.retry_cap_sec,
                        self._policy.retry_base_sec * 2**round_index,
                    )
                )

    async def _deliver_once(
        self, intent: MessageDeliveryIntent, attempt_id: uuid.UUID
    ) -> None:
        lease = await self._protocol.reserve(
            tenant_id=intent.tenant_id,
            message_key=intent.message_key,
            target_key=intent.target_key,
            attempt_id=attempt_id,
            lease_sec=self._policy.lease_sec,
        )
        if lease.lease_until is None:
            record_outcome(CHANNEL_DELIVERY_METRIC, "SKIPPED")
            return
        send = asyncio.create_task(
            self._send_within_budget(
                intent, attempt_id=attempt_id, lease_epoch=lease.lease_epoch
            )
        )
        keep = asyncio.create_task(
            self._keep_lease(intent, attempt_id, lease.lease_epoch)
        )
        try:
            done, _ = await asyncio.wait(
                {send, keep}, return_when=asyncio.FIRST_COMPLETED
            )
            if keep in done:
                send.cancel()
                await _settle(send)
                keep.result()
            outcome = await send
        finally:
            await _cancel(keep)
        await self._protocol.ack(
            tenant_id=intent.tenant_id,
            delivery_id=intent.delivery_id,
            message_key=intent.message_key,
            target_key=intent.target_key,
            attempt_id=attempt_id,
            epoch=lease.lease_epoch,
            outcome=outcome.outcome,
            result=outcome.result,
        )
        record_outcome(CHANNEL_DELIVERY_METRIC, outcome.outcome)

    async def _send_within_budget(
        self,
        intent: MessageDeliveryIntent,
        *,
        attempt_id: uuid.UUID,
        lease_epoch: int,
    ) -> MessageSendOutcome:
        try:
            return await asyncio.wait_for(
                self._sender.send(
                    intent, attempt_id=attempt_id, lease_epoch=lease_epoch
                ),
                timeout=self._policy.timeout_sec,
            )
        except TimeoutError:
            return MessageSendOutcome(
                outcome=MESSAGE_UNKNOWN, result={"reason": "timeout"}
            )

    async def _keep_lease(
        self, intent: MessageDeliveryIntent, attempt_id: uuid.UUID, epoch: int
    ) -> None:
        period = max(self._policy.lease_sec / 3, 0.5)
        while True:
            await asyncio.sleep(period)
            await self._protocol.renew(
                tenant_id=intent.tenant_id,
                delivery_id=intent.delivery_id,
                message_key=intent.message_key,
                target_key=intent.target_key,
                attempt_id=attempt_id,
                epoch=epoch,
                lease_sec=self._policy.lease_sec,
            )


class GatewayMessageDeliverySender:
    """经既有 Gateway `/internal/deliveries` 发送冻结消息（正文 + 多附件）。"""

    def __init__(self, client: MessageDeliveryTransportPort) -> None:
        self._client = client

    async def send(
        self,
        intent: MessageDeliveryIntent,
        *,
        attempt_id: uuid.UUID,
        lease_epoch: int,
    ) -> MessageSendOutcome:
        if intent.route is None:
            return MessageSendOutcome(
                outcome=MESSAGE_FAILED, result={"code": "DELIVERY_ROUTE_INVALID"}
            )
        payload = intent.payload
        message = _delivery_message(intent, payload)
        if message is None:
            return MessageSendOutcome(
                outcome=MESSAGE_PENDING, result={"reason": "text_missing"}
            )
        request = DeliveryRequest(
            tenant_id=intent.tenant_id,
            task_id=_task_id_of(intent.message_key),
            delivery_key=intent.message_key,
            route=intent.route,
            message=message,
            artifact_ids=[
                attachment.artifact_id
                for attachment in payload.attachments
                if attachment.artifact_id is not None
            ],
            message_id=intent.message_id,
            delivery_attempt_id=attempt_id,
            delivery_epoch=lease_epoch,
            target_key=intent.target_key,
            agent_id=intent.agent_id,
            platform_user_id=intent.platform_user_id,
        )
        try:
            response = await self._client.deliver(request)
        except DeliveryNotSentError as exc:
            return MessageSendOutcome(
                outcome=MESSAGE_PENDING, result={"reason": str(exc)}
            )
        except DeliveryUnavailableError as exc:
            return MessageSendOutcome(
                outcome=MESSAGE_UNKNOWN, result={"reason": str(exc)}
            )
        if response.outcome == "DEGRADED":
            return MessageSendOutcome(
                outcome=MESSAGE_DEGRADED,
                result={
                    "fallback_url": response.fallback_url,
                    "delivered": bool(response.delivered),
                    "deduplicated": response.deduplicated,
                },
            )
        if not response.delivered:
            return MessageSendOutcome(
                outcome=MESSAGE_PENDING,
                result={"reason": "gateway_placeholder"},
            )
        return MessageSendOutcome(
            outcome=MESSAGE_DELIVERED,
            result={
                "delivered": True,
                "deduplicated": response.deduplicated,
            },
        )


def _intent(row: MessageDelivery) -> MessageDeliveryIntent:
    try:
        route: DeliveryRouteInput | None = delivery_route_of_row(row)
    except Exception:
        logger.warning(
            "message_delivery_route_invalid", extra={"delivery_id": str(row.id)}
        )
        route = None
    return MessageDeliveryIntent(
        delivery_id=row.id,
        tenant_id=row.tenant_id,
        message_key=row.message_key,
        target_key=row.target_key,
        kind=row.kind,
        owner_type=row.owner_type,
        owner_id=row.owner_id,
        route=route,
        payload=delivery_payload_of(row),
        attempt=int(row.attempt_count),
        message_id=row.message_id,
        agent_id=row.agent_id,
        platform_user_id=row.platform_user_id,
    )


def _delivery_message(
    intent: MessageDeliveryIntent, payload: MessageDeliveryPayload
) -> DeliveryMessage | None:
    if intent.kind == "TERMINAL":
        return DeliveryMessage(
            type="terminal",
            text=payload.text,
            attachments=list(payload.attachments),
            status=payload.status,
            error_code=payload.error_code,
        )
    if not payload.text and not payload.attachments:
        return None
    if payload.attachments:
        return DeliveryMessage(
            type="message",
            text=payload.text,
            attachments=list(payload.attachments),
        )
    return DeliveryMessage(type="text", text=payload.text)


def _task_id_of(message_key: str) -> uuid.UUID | None:
    parts = message_key.split(":")
    if len(parts) >= 2 and parts[0] == "execution":
        try:
            return uuid.UUID(parts[1])
        except ValueError:
            return None
    return None


async def _cancel(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    await _settle(task)


async def _settle(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return


#: 显式导出，供任务/测试装配直接引用（避免再从 state 字段推断）。
__all__ = [
    "GatewayMessageDeliverySender",
    "MessageDeliveryIntent",
    "MessageDeliveryPump",
    "MessageDeliveryService",
    "MessageSendOutcome",
]
