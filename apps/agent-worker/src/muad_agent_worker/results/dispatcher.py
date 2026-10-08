"""PostgreSQL leased result delivery; attempts are committed before network IO."""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from muad_common import SharedSettings
from muad_contracts import ControlOutboxStatus
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.runtime_operations import RuntimeResultOutbox
from ..metrics import record_outcome

logger = logging.getLogger(__name__)
RESULT_DISPATCH_METRIC = "runtime_result_dispatch_total"


class ResultDispatchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timeout_sec: float = Field(default=5, gt=0)
    lease_sec: float = Field(default=30, gt=5)
    poll_sec: float = Field(default=1, gt=0)
    batch_size: int = Field(default=32, ge=1, le=1000)
    retry_base_sec: float = Field(default=1, gt=0)
    retry_cap_sec: float = Field(default=30, gt=0)
    max_attempts: int = Field(default=20, ge=1)

    @classmethod
    def from_settings(cls, settings: SharedSettings) -> "ResultDispatchPolicy":
        return cls(
            timeout_sec=settings.async_tool_dispatch_timeout_sec,
            lease_sec=settings.async_tool_dispatch_lease_sec,
            poll_sec=settings.async_tool_dispatch_poll_sec,
            batch_size=settings.async_tool_dispatch_batch_size,
            retry_base_sec=settings.async_tool_dispatch_retry_base_sec,
            retry_cap_sec=settings.async_tool_dispatch_retry_cap_sec,
            max_attempts=settings.async_tool_dispatch_max_attempts,
        )


class ResultDispatcher:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient,
        runtime_url: str,
        *,
        service_token: str | None = None,
        policy: ResultDispatchPolicy | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._factory, self._client, self._url = factory, client, runtime_url.rstrip("/")
        self._token, self._policy = service_token, policy or ResultDispatchPolicy()
        self._owner = instance_id or f"result-dispatch:{uuid4()}"
        self._cursor: tuple[datetime, datetime, UUID] | None = None
        if self._policy.lease_sec <= self._policy.timeout_sec:
            raise ValueError("result lease must exceed HTTP timeout")

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("runtime_result_dispatch_tick_failed")
                record_outcome(RESULT_DISPATCH_METRIC, "SCAN_FAILED")
            await asyncio.sleep(self._policy.poll_sec)

    async def claim(self, now: datetime) -> list[RuntimeResultOutbox]:
        async with self._factory() as session, session.begin():
            rows = await self._candidates(session, now)
            if not rows and self._cursor is not None:
                self._cursor = None
                rows = await self._candidates(session, now)
            if rows:
                last = rows[-1]
                self._cursor = (last.not_before, last.create_time, last.id)
            claimed = []
            for row in rows:
                if row.attempts >= self._policy.max_attempts:
                    row.status, row.last_error_code = ControlOutboxStatus.FAILED, "RESULT_ATTEMPTS_EXHAUSTED"
                    self._alert(row, row.last_error_code)
                else:
                    row.attempts += 1
                    row.lease_owner, row.lease_until = (
                        self._owner,
                        now + timedelta(seconds=self._policy.lease_sec),
                    )
                    claimed.append(row)
                row.update_time = now
            await session.flush()
            # Session factories need not have expire_on_commit=False.
            for row in rows:
                session.expunge(row)
        return claimed

    async def _candidates(self, session: AsyncSession, now: datetime) -> list[RuntimeResultOutbox]:
        ordering = (RuntimeResultOutbox.not_before, RuntimeResultOutbox.create_time, RuntimeResultOutbox.id)
        conditions = [
            RuntimeResultOutbox.status == ControlOutboxStatus.PENDING,
            RuntimeResultOutbox.is_deleted.is_(False),
            RuntimeResultOutbox.not_before <= now,
            or_(RuntimeResultOutbox.lease_until.is_(None), RuntimeResultOutbox.lease_until <= now),
        ]
        if self._cursor is not None:
            conditions.append(tuple_(*ordering) > self._cursor)
        return list(
            await session.scalars(
                select(RuntimeResultOutbox)
                .where(*conditions)
                .order_by(*ordering)
                .limit(self._policy.batch_size)
                .with_for_update(skip_locked=True)
            )
        )

    async def ack(self, row_id: UUID, lease_until: datetime | None, now: datetime) -> bool:
        async with self._factory() as session, session.begin():
            changed = await session.scalar(
                update(RuntimeResultOutbox)
                .where(
                    RuntimeResultOutbox.id == row_id,
                    RuntimeResultOutbox.lease_owner == self._owner,
                    RuntimeResultOutbox.lease_until == lease_until,
                    RuntimeResultOutbox.lease_until > now,
                    RuntimeResultOutbox.status == ControlOutboxStatus.PENDING,
                    RuntimeResultOutbox.is_deleted.is_(False),
                )
                .values(
                    status=ControlOutboxStatus.SENT,
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=None,
                    update_time=now,
                )
                .returning(RuntimeResultOutbox.id)
            )
        if changed is not None:
            record_outcome(RESULT_DISPATCH_METRIC, "SENT")
        return changed is not None

    async def run_once(self, *, now: datetime | None = None) -> int:
        rows = await self.claim(now or datetime.now(UTC))
        # Claims are committed; no database lock survives the HTTP boundary.
        for row in rows:
            error, deterministic = await self._send(row)
            completed_at = now or datetime.now(UTC)
            if error is None:
                await self.ack(row.id, row.lease_until, completed_at)
            else:
                await self._reject(row, error, deterministic, completed_at)
        return len(rows)

    async def _send(self, row: RuntimeResultOutbox) -> tuple[str | None, bool]:
        headers = {"X-Tenant-Id": row.tenant_id, "Idempotency-Key": f"task-result:{row.event_id}"}
        if self._token:
            headers["X-Internal-Service"] = self._token
        try:
            response = await self._client.post(
                self._url + "/internal/tool-results",
                json=row.payload_json,
                headers=headers,
                timeout=self._policy.timeout_sec,
            )
        except httpx.HTTPError:
            return "RESULT_TRANSPORT_ERROR", False
        if 400 <= response.status_code < 500:
            return f"RESULT_HTTP_{response.status_code}", True
        if not 200 <= response.status_code < 300:
            return "RESULT_HTTP_RETRY", False
        try:
            envelope = response.json()
        except ValueError:
            return "RESULT_ACK_INVALID", False
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("persisted") is not True
            or data.get("event_id") != str(row.event_id)
        ):
            return "RESULT_ACK_INVALID", False
        return None, False

    async def _reject(self, row: RuntimeResultOutbox, error: str, deterministic: bool, now: datetime) -> None:
        failed = deterministic or row.attempts >= self._policy.max_attempts
        delay = min(self._policy.retry_cap_sec, self._policy.retry_base_sec * 2 ** min(row.attempts - 1, 20))
        delay *= random.uniform(0.8, 1.2)
        async with self._factory() as session, session.begin():
            changed = await session.scalar(
                update(RuntimeResultOutbox)
                .where(
                    RuntimeResultOutbox.id == row.id,
                    RuntimeResultOutbox.lease_owner == self._owner,
                    RuntimeResultOutbox.lease_until == row.lease_until,
                    RuntimeResultOutbox.lease_until > now,
                    RuntimeResultOutbox.status == ControlOutboxStatus.PENDING,
                    RuntimeResultOutbox.is_deleted.is_(False),
                )
                .values(
                    status=ControlOutboxStatus.FAILED if failed else ControlOutboxStatus.PENDING,
                    not_before=now + timedelta(seconds=delay),
                    lease_owner=None,
                    lease_until=None,
                    last_error_code=error,
                    update_time=now,
                )
                .returning(RuntimeResultOutbox.id)
            )
        if changed is not None:
            if failed:
                self._alert(row, error)
            else:
                logger.warning(
                    "runtime_result_dispatch_retry", extra={"error_code": error, "attempt": row.attempts}
                )
                record_outcome(RESULT_DISPATCH_METRIC, "RETRY")

    @staticmethod
    def _alert(row: RuntimeResultOutbox, error: str) -> None:
        logger.error("runtime_result_dispatch_failed", extra={"error_code": error, "attempt": row.attempts})
        record_outcome(RESULT_DISPATCH_METRIC, "FAILED")
