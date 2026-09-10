import asyncio
import logging

logger = logging.getLogger("worker.engine")


class WorkerEngine:
    """Durable execution loop boundary.

    PostgreSQL claim/lease/retry/recovery implementations belong behind repositories.
    This scaffold intentionally does not fake durability with an in-memory queue.
    """

    async def run_forever(self) -> None:
        logger.info("worker_started", extra={"event": "worker_started"})
        while True:
            # TODO: claim PENDING/due WAITING/RETRY_WAIT/expired RUNNING via
            # SELECT ... FOR UPDATE SKIP LOCKED, then execute one durable step.
            await asyncio.sleep(1.0)
