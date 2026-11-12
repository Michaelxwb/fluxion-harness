"""One terminal, metadata-only observation per physical request attempt."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .shared_redis import MeteringUnavailable, SharedLimitExceeded

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EgressAttempt:
    attempt_id: str
    origin: str
    route_id: str
    method: str
    sent: bool
    result_status: str
    status_code: int | None
    latency_ms: int
    error_code: str | None


AttemptObserver = Callable[[EgressAttempt], Awaitable[None]]


class AttemptAudit:
    def __init__(self, observe: AttemptObserver | None, *, origin: str, path: str, method: str) -> None:
        self.attempt_id = uuid.uuid4().hex
        self.sent = False
        self.status_code: int | None = None
        self.error_code: str | None = None
        self._observe, self._origin, self._method = observe, origin, method
        self._route_id = f"{method} {path}"
        self._started = time.monotonic()
        self._finished = False

    async def finish(self, error: BaseException | None = None) -> None:
        if self._observe is None or self._finished:
            return
        self._finished = True
        status, code = self._outcome(error)
        event = EgressAttempt(
            self.attempt_id, self._origin, self._route_id, self._method, self.sent, status,
            self.status_code, max(0, int((time.monotonic() - self._started) * 1000)), code,
        )
        try:
            await self._observe(event)
        except Exception as exc:
            # Observation failure cannot turn a denial into admission or retry an HTTP send.
            logger.warning("egress_attempt_audit_failed", extra={"error_type": type(exc).__name__})

    def _outcome(self, error: BaseException | None) -> tuple[str, str | None]:
        if isinstance(error, SharedLimitExceeded):
            return "BLOCKED", "LIMIT_" + error.kind
        if isinstance(error, asyncio.CancelledError):
            return "FAILED", "CANCELLED"
        if isinstance(error, MeteringUnavailable):
            return "FAILED", "METERING_UNAVAILABLE"
        if isinstance(error, TimeoutError):
            return "FAILED", "TIMEOUT"
        if error is not None or self.error_code is not None:
            return "FAILED", self.error_code or "TRANSPORT_ERROR"
        if self.status_code is not None and self.status_code >= 400:
            return "FAILED", "HTTP_ERROR"
        return "COMPLETED", None
