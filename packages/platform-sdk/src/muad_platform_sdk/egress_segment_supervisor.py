"""Source-authorized renewal and bounded shutdown of a single host execution.

The caller must own all work/clients in this segment. Its close_clients receipt
must confirm child-group and host socket termination, not just cancel IPC tasks.
No app lease acquisition, policy initialization or business retries happen here.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable

from .egress_identity_registry import IdentityUnavailable
from .egress_segments import FencedRedisMeter
from .egress_source_authority import PGSegmentAuthority, SourceClaim, SourceReader
from .shared_redis import MeteringUnavailable

logger = logging.getLogger(__name__)


class SourceSegmentSupervisor:
    def __init__(self, *, authority: PGSegmentAuthority, execution_id: uuid.UUID,
                 claim: SourceClaim, reader: SourceReader, meter: FencedRedisMeter,
                 refresh_seconds: float, stop_margin_seconds: float,
                 close_clients: Callable[[], Awaitable[None]]) -> None:
        if any(not math.isfinite(v) or v <= 0 for v in (refresh_seconds, stop_margin_seconds)):
            raise ValueError("positive finite refresh and stop margin required")
        if str(execution_id) != meter.grant.execution_id:
            raise ValueError("source execution and bound meter must agree")
        self._authority, self._execution_id = authority, execution_id
        self._claim, self._reader, self._meter = claim, reader, meter
        self._refresh_seconds, self._stop_margin = refresh_seconds, stop_margin_seconds
        self._close_clients = close_clients
        self._stop_at: float | None = None
        self._used = False

    async def _refresh(self) -> None:
        # The OLD deadline also bounds PG lock waits and Redis command latency.
        limit = time.monotonic() + self._refresh_seconds
        if self._stop_at is not None:
            limit = min(limit, self._stop_at)
        try:
            async with asyncio.timeout_at(limit):
                bound = await self._authority.authorize(self._execution_id, self._claim, self._reader)
                if bound.grant != self._meter.grant:
                    raise IdentityUnavailable("source authorization changed execution segment")
                stop_at = bound.local_deadline - self._stop_margin
                if stop_at <= time.monotonic():
                    raise IdentityUnavailable("source lease has no safe execution interval")
                async with asyncio.timeout_at(stop_at):
                    await self._meter.activate(lease_seconds=bound.remaining_seconds(),
                                               stop_margin_seconds=self._stop_margin)
                if time.monotonic() >= stop_at:
                    raise IdentityUnavailable("source lease expired during activation")
                self._stop_at = stop_at
        except TimeoutError as exc:
            raise IdentityUnavailable("source renewal exceeded safe deadline") from exc

    async def _watch(self) -> None:
        while self._stop_at is not None:
            remaining = self._stop_at - time.monotonic()
            if remaining <= 0:
                raise IdentityUnavailable("source execution lease reached stop deadline")
            await asyncio.sleep(min(self._refresh_seconds, remaining))
            await self._refresh()

    async def _stop[T](self, work: asyncio.Task[T]) -> None:
        if not work.done():
            work.cancel()
        close = asyncio.create_task(_invoke(self._close_clients))
        try:
            # wait() also bounds a close callback that suppresses cancellation.
            _, pending = await asyncio.wait({work, close}, timeout=self._stop_margin)
            if pending:
                raise IdentityUnavailable("execution termination not confirmed")
            close.result()
            await asyncio.gather(work, return_exceptions=True)
        except Exception as exc:
            raise IdentityUnavailable("execution termination not confirmed") from exc
        finally:
            for task in (work, close):
                if not task.done():
                    task.cancel()
                task.add_done_callback(_retrieve_result)
        try:
            await asyncio.wait_for(self._meter.confirm_stopped(), self._stop_margin)
        except MeteringUnavailable:
            # Local closure is established. Keep Redis conservative on uncertainty.
            logger.warning("egress_segment_stop_receipt_unavailable")
        except TimeoutError:
            logger.warning("egress_segment_stop_receipt_timeout")

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._used:
            raise IdentityUnavailable("execution supervisor is single-use")
        self._used = True
        await self._refresh()  # Never start business without both PG and Redis grants.
        work = asyncio.create_task(_invoke(operation))
        watch = asyncio.create_task(self._watch())
        try:
            await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
            if watch.done():
                await watch
            if self._stop_at is None or time.monotonic() >= self._stop_at:
                raise IdentityUnavailable("source execution lease reached stop deadline")
            return await work
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
            await self._stop(work)


def _retrieve_result[T](task: asyncio.Future[T]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("egress_segment_unconfirmed_work_failed")


async def _invoke[T](operation: Callable[[], Awaitable[T]]) -> T:
    return await operation()
