"""Leased, bounded control delivery. No business lock spans an HTTP request."""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from muad_common import SharedSettings
from muad_contracts import (
    ControlCommand,
    ControlOutboxStatus,
    OperationErrorPhase,
    OperationStatus,
    TaskStatus,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
from ...infrastructure.models.runtime import RunRecord
from ...metrics import record_outcome
from ..run_events import EventWriter
from ..tool_result_receipts import OP_TERMINAL, RUN_TERMINAL, lock_run_source
from .operations import add_cancel_command

logger = logging.getLogger(__name__)
METRIC = "tool_control_dispatch_total"
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class ControlDispatchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timeout_sec: float = Field(default=5, gt=0)
    lease_sec: float = Field(default=30, gt=5)
    poll_sec: float = Field(default=1, gt=0)
    batch_size: int = Field(default=32, ge=1, le=1000)
    retry_base_sec: float = Field(default=1, gt=0)
    retry_cap_sec: float = Field(default=30, gt=0)
    max_attempts: int = Field(default=20, ge=1)

    @classmethod
    def from_settings(cls, settings: SharedSettings) -> "ControlDispatchPolicy":
        return cls(**{key: getattr(settings, f"async_tool_dispatch_{key}") for key in cls.model_fields})


class ControlDispatcher:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient,
        worker_url: str,
        *,
        service_token: str | None = None,
        policy: ControlDispatchPolicy | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._factory, self._client, self._url = factory, client, worker_url.rstrip("/")
        self._token, self._policy = service_token, policy or ControlDispatchPolicy()
        self._owner = instance_id or f"control:{uuid4()}"
        self._cursor: tuple[datetime, datetime, UUID] | None = None
        if self._policy.lease_sec <= self._policy.timeout_sec:
            raise ValueError("control lease must exceed HTTP timeout")

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("tool_control_dispatch_tick_failed")
                record_outcome(METRIC, "SCAN_FAILED")
            await asyncio.sleep(self._policy.poll_sec)

    async def claim(self, now: datetime, operation_id: UUID | None = None) -> list[ToolControlOutbox]:
        async with self._factory() as session, session.begin():
            rows = await self._candidates(session, now, operation_id)
            if not rows and operation_id is None and self._cursor is not None:
                self._cursor = None
                rows = await self._candidates(session, now, operation_id)
            if rows and operation_id is None:
                last = rows[-1]
                self._cursor = (last.not_before, last.create_time, last.id)
            for row in rows:
                if row.attempts < self._policy.max_attempts:
                    row.attempts += 1
                else:
                    row.last_error_code = "CONTROL_ATTEMPTS_EXHAUSTED"
                row.lease_owner, row.lease_until = (
                    self._owner,
                    now + timedelta(seconds=self._policy.lease_sec),
                )
                row.update_time = now
            await session.flush()
            for row in rows:
                session.expunge(row)
        return rows

    async def _candidates(
        self, session: AsyncSession, now: datetime, operation_id: UUID | None
    ) -> list[ToolControlOutbox]:
        ordering = (ToolControlOutbox.not_before, ToolControlOutbox.create_time, ToolControlOutbox.id)
        conditions = [
            ToolControlOutbox.status == ControlOutboxStatus.PENDING,
            ToolControlOutbox.is_deleted.is_(False),
            ToolControlOutbox.not_before <= now,
            or_(ToolControlOutbox.lease_until.is_(None), ToolControlOutbox.lease_until <= now),
        ]
        if operation_id is not None:
            conditions.append(ToolControlOutbox.operation_id == operation_id)
        elif self._cursor is not None:
            conditions.append(tuple_(*ordering) > self._cursor)
        return list(
            await session.scalars(
                select(ToolControlOutbox)
                .where(*conditions)
                .order_by(*ordering)
                .limit(self._policy.batch_size)
                .with_for_update(skip_locked=True)
            )
        )

    async def run_once(self, *, now: datetime | None = None) -> int:
        rows = await self.claim(now or datetime.now(UTC))
        for row in rows:
            await self._dispatch(row, now)
        return len(rows)

    async def dispatch_operation(self, operation_id: UUID) -> None:
        for row in await self.claim(datetime.now(UTC), operation_id):
            await self._dispatch(row, None)

    async def _dispatch(self, row: ToolControlOutbox, now: datetime | None) -> None:
        if row.last_error_code == "CONTROL_ATTEMPTS_EXHAUSTED":
            await self._complete(row, None, "CONTROL_ATTEMPTS_EXHAUSTED", True, now or datetime.now(UTC))
            return
        async with self._factory() as session:
            operation = await session.scalar(
                select(ToolOperation).where(
                    ToolOperation.id == row.operation_id,
                    ToolOperation.tenant_id == row.tenant_id,
                    ToolOperation.is_deleted.is_(False),
                )
            )
            if operation is None:
                raise RuntimeError("control command has no operation")
            session.expunge(operation)
        if row.command == ControlCommand.SUBMIT and (
            operation.cancel_requested or operation.status in OP_TERMINAL
        ):
            await self._complete(row, None, "SUBMISSION_ABANDONED", True, now or datetime.now(UTC))
            return
        data, error, deterministic = await self._send(row, operation)
        await self._complete(row, data, error, deterministic, now or datetime.now(UTC))

    async def _send(
        self, row: ToolControlOutbox, operation: ToolOperation
    ) -> tuple[dict[str, JsonValue] | None, str | None, bool]:
        submit = row.command == ControlCommand.SUBMIT
        path = "/internal/tasks" if submit else f"/internal/runtime-operations/{operation.id}/cancel"
        body = (
            operation.submission_json
            if submit
            else {
                "source_run_id": str(operation.run_id),
                "source_tool_call_id": operation.source_tool_call_id,
                "actor_user_id": str(operation.actor_user_id),
            }
        )
        headers = {
            "X-Tenant-Id": row.tenant_id,
            "X-Actor-User-Id": str(operation.actor_user_id),
            "Idempotency-Key": f"runtime-op:{operation.id}:{'submit' if submit else 'cancel'}",
        }
        if self._token:
            headers["X-Internal-Service"] = self._token
        try:
            response = await self._client.post(
                self._url + path, json=body, headers=headers, timeout=self._policy.timeout_sec
            )
        except httpx.HTTPError:
            return None, "CONTROL_TRANSPORT_ERROR", False
        if response.status_code >= 500:
            return None, "CONTROL_HTTP_RETRY", False
        if 400 <= response.status_code < 500:
            return None, f"CONTROL_HTTP_{response.status_code}", True
        try:
            envelope = response.json()
            data = JSON_OBJECT.validate_python(envelope["data"])
            if not 200 <= response.status_code < 300 or not self._valid_ack(data, operation, submit):
                return None, "CONTROL_ACK_INVALID", False
        except (ValueError, KeyError, TypeError):
            return None, "CONTROL_ACK_INVALID", False
        return data, None, False

    @staticmethod
    def _valid_ack(data: dict[str, JsonValue], operation: ToolOperation, submit: bool) -> bool:
        if not submit:
            return data.get("operation_id") == str(operation.id) and data.get("cancel_recorded") is True
        try:
            task_id = UUID(str(data.get("task_id")))
            TaskStatus(str(data.get("status")))
        except ValueError:
            return False
        return (
            data.get("source_run_id") == str(operation.run_id)
            and data.get("actor_user_id") == str(operation.actor_user_id)
            and data.get("source_operation_id") == str(operation.id)
            and data.get("source_tool_call_id") == operation.source_tool_call_id
            and data.get("snapshot_hash") == operation.task_snapshot_hash
            and (operation.task_id is None or operation.task_id == task_id)
        )

    async def _locked(
        self, session: AsyncSession, claim: ToolControlOutbox, now: datetime
    ) -> tuple[RunRecord, ToolOperation, ToolControlOutbox] | None:
        source = await session.scalar(
            select(ToolOperation).where(
                ToolOperation.id == claim.operation_id,
                ToolOperation.tenant_id == claim.tenant_id,
                ToolOperation.is_deleted.is_(False),
            )
        )
        if source is None:
            raise RuntimeError("control source disappeared")
        run = await session.scalar(
            select(RunRecord).where(
                RunRecord.id == source.run_id,
                RunRecord.tenant_id == claim.tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None:
            raise RuntimeError("control Run disappeared")
        run, _ = await lock_run_source(session, claim.tenant_id, run)
        operation = (
            await session.scalars(
                select(ToolOperation)
                .where(
                    ToolOperation.id == source.id,
                    ToolOperation.tenant_id == claim.tenant_id,
                    ToolOperation.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        row = await session.scalar(
            select(ToolControlOutbox)
            .where(
                ToolControlOutbox.id == claim.id,
                ToolControlOutbox.tenant_id == claim.tenant_id,
                ToolControlOutbox.is_deleted.is_(False),
                ToolControlOutbox.status == ControlOutboxStatus.PENDING,
                ToolControlOutbox.lease_owner == self._owner,
                ToolControlOutbox.lease_until == claim.lease_until,
                ToolControlOutbox.lease_until > now,
            )
            .with_for_update()
        )
        return (run, operation, row) if row is not None else None

    async def _complete(
        self,
        claim: ToolControlOutbox,
        data: dict[str, JsonValue] | None,
        error: str | None,
        deterministic: bool,
        now: datetime,
    ) -> None:
        async with self._factory() as session, session.begin():
            locked = await self._locked(session, claim, now)
            if locked is None:
                return
            run, operation, row = locked
            row.lease_owner, row.lease_until, row.update_time = None, None, now
            if error is None and data is not None:
                row.status, row.last_error_code = ControlOutboxStatus.SENT, None
                if row.command == ControlCommand.SUBMIT:
                    await _admit(session, run, operation, data, now)
                record_outcome(METRIC, "SENT")
            elif deterministic or row.attempts >= self._policy.max_attempts:
                row.status, row.last_error_code = ControlOutboxStatus.FAILED, error
                if row.command == ControlCommand.SUBMIT and operation.status not in OP_TERMINAL:
                    await _fail_submission(session, run, operation, error or "SUBMISSION_UNCONFIRMED", now)
                logger.error(
                    "tool_control_dispatch_failed", extra={"command": row.command.value, "error_code": error}
                )
                record_outcome(METRIC, "FAILED")
            else:
                delay = min(
                    self._policy.retry_cap_sec, self._policy.retry_base_sec * 2 ** min(row.attempts - 1, 20)
                )
                row.not_before, row.last_error_code = (
                    now + timedelta(seconds=delay * random.uniform(0.8, 1.2)),
                    error,
                )
                logger.warning(
                    "tool_control_dispatch_retry", extra={"command": row.command.value, "error_code": error}
                )
                record_outcome(METRIC, "RETRY")

    @staticmethod
    def report_pending_timeout() -> None:
        logger.warning("tool_admission_wait_expired")
        record_outcome(METRIC, "ADMISSION_PENDING")


async def _admit(
    session: AsyncSession, run: RunRecord, operation: ToolOperation, data: dict[str, JsonValue], now: datetime
) -> None:
    task_id = UUID(str(data["task_id"]))
    if operation.task_id is not None and operation.task_id != task_id:
        raise ValueError("admission task binding changed")
    late = (
        operation.cancel_requested
        or operation.status in OP_TERMINAL
        or run.cancel_requested
        or run.status in RUN_TERMINAL
    )
    operation.task_id, operation.submitted_at, operation.update_time = task_id, now, now
    if late:
        operation.status = OperationStatus.LATE
        await add_cancel_command(session, operation, now)
    elif operation.status == OperationStatus.SUBMIT_PENDING:
        operation.status = OperationStatus.SUBMITTED
    await EventWriter(session).append(
        tenant_id=run.tenant_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="BACKGROUND_RESULT_LATE" if late else "TOOL_TASK_ACCEPTED",
        stream_type="tool.result.late" if late else "tool.submitted",
        source_event_id=uuid5(NAMESPACE_URL, f"muad:tool-operation:{operation.id}:admission"),
        payload={
            "event_version": 1,
            "operation_id": str(operation.id),
            "task_id": str(task_id),
            "task_status": data["status"],
            "completion_mode": operation.completion_mode.value,
        },
    )


async def _fail_submission(
    session: AsyncSession, run: RunRecord, operation: ToolOperation, error: str, now: datetime
) -> None:
    operation.status, operation.error_phase = OperationStatus.FAILED, OperationErrorPhase.SUBMIT
    operation.error_code, operation.completed_at, operation.update_time = "SUBMISSION_UNCONFIRMED", now, now
    await add_cancel_command(session, operation, now)
    await EventWriter(session).append(
        tenant_id=run.tenant_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="TOOL_SUBMISSION_FAILED",
        stream_type="tool.submission.failed",
        source_event_id=uuid5(NAMESPACE_URL, f"muad:tool-operation:{operation.id}:submission-failed"),
        payload={
            "event_version": 1,
            "operation_id": str(operation.id),
            "error_phase": "SUBMIT",
            "error_code": "SUBMISSION_UNCONFIRMED",
            "cause": error,
            "task_id": None,
        },
    )
    from ...infrastructure.models.async_tools import RunContinuation

    continuation = await session.scalar(
        select(RunContinuation).where(
            RunContinuation.run_id == run.id,
            RunContinuation.tenant_id == run.tenant_id,
            RunContinuation.is_deleted.is_(False),
        )
    )
    if continuation is not None:
        continuation.ready, continuation.update_time = True, now
