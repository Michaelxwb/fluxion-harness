"""Host-owned HTTP streaming primitive; Runtime/Worker wiring is a separate step.

Callers must supply an authorized prepared request and a trusted client without
hidden retries/auth exchanges. Redirects and client auth flows are disabled here
so each invocation has one send boundary. Cooperative asyncio deadlines cannot
replace the execution supervisor's process fencing/termination.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation

from .egress_attempt_audit import AttemptAudit, AttemptObserver
from .shared_redis import MeteringUnavailable, SharedPermit, SharedRedisMeter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RequestLifetime:
    timeout_seconds: float
    cleanup_seconds: float
    renewal_interval_seconds: float

    def __post_init__(self) -> None:
        values = (self.timeout_seconds, self.cleanup_seconds, self.renewal_interval_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("request lifetime values must be finite and positive")
        if not math.isfinite(self.timeout_seconds + self.cleanup_seconds):
            raise ValueError("request lifetime exceeds the supported range")
        if self.renewal_interval_seconds >= self.timeout_seconds:
            raise ValueError("renewal interval must be shorter than the request deadline")


class _LeaseWatch:
    def __init__(self, meter: SharedRedisMeter, permit: SharedPermit,
                 lifetime: RequestLifetime, deadline: float, timer: asyncio.Timeout) -> None:
        self.meter, self.permit = meter, permit
        self.lifetime, self.deadline, self.timer = lifetime, deadline, timer
        self.failure: MeteringUnavailable | None = None
        self.task = asyncio.create_task(self._run(), name="shared-egress-lease-watch")

    async def _run(self) -> None:
        interval = self.lifetime.renewal_interval_seconds
        try:
            while True:
                await asyncio.sleep(interval)
                remaining = self.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                async with asyncio.timeout(min(interval, remaining)):
                    await self.meter.renew(
                        self.permit, lease_seconds=remaining + self.lifetime.cleanup_seconds,
                    )
        except (MeteringUnavailable, TimeoutError) as exc:
            self.failure = MeteringUnavailable("shared lease supervision unavailable")
            self.failure.__cause__ = exc
            if not self.timer.expired():
                self.timer.reschedule(asyncio.get_running_loop().time())

    async def stop(self) -> None:
        self.task.cancel()
        outcomes = await asyncio.gather(self.task, return_exceptions=True)
        if isinstance(outcomes[0], Exception):
            raise outcomes[0]


class SharedHTTPTransport:
    """Own one stream until scope exit, including cancellation and dependency loss."""

    def __init__(self, meter: SharedRedisMeter, *, lifetime: RequestLifetime,
                 observe: AttemptObserver | None = None) -> None:
        self._meter, self._lifetime, self._observe = meter, lifetime, observe

    @asynccontextmanager
    async def stream(
        self, client: httpx.AsyncClient, request: httpx.Request, *,
        policy: SharedEgressPolicy, target_instance_id: str, route_id: str,
        invocation: TrustedInvocation | None = None,
    ) -> AsyncIterator[httpx.Response]:
        origin = str(request.url.copy_with(path="/", query=None)).rstrip("/")
        audit = AttemptAudit(
            self._observe, origin=origin, method=request.method,
            path=request.url.raw_path.split(b"?", 1)[0].decode("ascii"),
        )
        error: BaseException | None = None
        permit: SharedPermit | None = None
        response: httpx.Response | None = None
        watch: _LeaseWatch | None = None
        deadline = asyncio.get_running_loop().time() + self._lifetime.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline) as timer:
                permit = await self._meter.admit(
                    policy, target_instance_id=target_instance_id, route_id=route_id,
                    attempt_id=audit.attempt_id, invocation=invocation,
                    lease_seconds=self._lifetime.timeout_seconds + self._lifetime.cleanup_seconds,
                )
                if permit is not None:
                    await self._meter.consume(permit)
                    watch = _LeaseWatch(self._meter, permit, self._lifetime, deadline, timer)
                audit.sent = True
                response = await client.send(request, stream=True, follow_redirects=False, auth=None)
                audit.status_code = response.status_code
                yield response
                if watch is not None and watch.failure is not None:
                    raise watch.failure
        except TimeoutError as exc:
            error = exc
            if watch is not None and watch.failure is not None:
                raise watch.failure from exc
            if not audit.sent:
                raise MeteringUnavailable("shared admission deadline exceeded") from exc
            raise
        except BaseException as exc:
            error = exc
            raise
        finally:
            await self._finish(watch, response, permit, audit, error or sys.exception())

    async def _finish(self, watch: _LeaseWatch | None, response: httpx.Response | None,
                      permit: SharedPermit | None, audit: AttemptAudit, error: BaseException | None) -> None:
        try:
            try:
                if watch is not None:
                    await watch.stop()
            finally:
                await self._cleanup(response, permit, failed=error is not None)
        finally:
            await audit.finish(error or sys.exception())

    async def _cleanup(
        self, response: httpx.Response | None, permit: SharedPermit | None, *, failed: bool,
    ) -> None:
        try:
            async with asyncio.timeout(self._lifetime.cleanup_seconds):
                if response is not None:
                    await response.aclose()
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            # Keep the lease if local transport closure could not be confirmed.
            logger.warning("shared_egress_transport_cleanup_failed")
            if not failed:
                raise MeteringUnavailable("controlled transport closure unconfirmed") from exc
            return
        if permit is not None:
            try:
                async with asyncio.timeout(self._lifetime.cleanup_seconds):
                    await self._meter.release(permit)
            except (MeteringUnavailable, TimeoutError):
                # The socket is closed. Keep occupancy conservatively until expiry;
                # a release failure must not rewrite an already received Proxy result.
                logger.warning("shared_egress_lease_release_failed")
