"""Execute external IO outside all row locks, then atomically apply fenced effects."""

import logging

import httpx
from muad_agent_core.model import ModelRateLimitedError, ModelUnavailableError
from muad_api import AppError
from pydantic import JsonValue
from sqlalchemy.exc import SQLAlchemyError

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import RuntimeSnapshot
from .memory_consolidation import ConsolidationInputTooLarge, ConsolidationInvoker
from .memory_consolidation_input import ConsolidationStale
from .memory_consolidation_output import ConsolidationBatch, ConsolidationRejected
from .memory_job_effects import ConsolidationFenced, apply_batch, fail_locked, terminal_result
from .memory_jobs import JobClaim, MemoryJobs, lock_claim
from .memory_observability import observe

logger = logging.getLogger(__name__)


class ConsolidationWorker:
    def __init__(self, factory: SessionFactory, invoker: ConsolidationInvoker) -> None:
        self.factory, self.invoker, self.jobs = factory, invoker, MemoryJobs(factory)

    async def execute(self, claim: JobClaim) -> dict[str, JsonValue] | None:
        try:
            async with self.factory.begin() as session:
                job = await lock_claim(session, claim)
                if job is None:
                    return await terminal_result(session, claim)
                snapshot = await session.get(RuntimeSnapshot, job.snapshot_id)
                if snapshot is None:
                    raise ConsolidationRejected()
            if claim.exhausted:
                return await self.fail(claim, reason="FAILED")
            async with self.invoker.open(job, snapshot) as invocation:
                payload = await self.jobs.freeze_input(claim, credential_canaries=invocation.canaries)
                if payload is None:
                    return None
                batch = await invocation.generate(payload)
                return await self.complete(claim, batch, credential_canaries=invocation.canaries)
        except (ConsolidationStale, ConsolidationInputTooLarge, ConsolidationRejected) as exc:
            return await self._failure(claim, str(exc))
        except ConsolidationFenced:
            observe("memory_consolidation_total", labels={"outcome": "FENCED"})
            logger.warning("memory_consolidation_fenced", extra={"job_id": str(claim.id)})
            return None
        except ValueError as exc:
            return await self._failure(claim, "REJECTED", error_type=type(exc).__name__)
        except (
            TimeoutError,
            httpx.HTTPError,
            ModelRateLimitedError,
            ModelUnavailableError,
            AppError,
            SQLAlchemyError,
        ) as exc:
            return await self._failure(claim, "FAILED", error_type=type(exc).__name__)

    async def complete(
        self, claim: JobClaim, batch: ConsolidationBatch, *, credential_canaries: tuple[str, ...]
    ) -> dict[str, JsonValue] | None:
        async with self.factory.begin() as session:
            return await apply_batch(session, claim, batch, credential_canaries=credential_canaries)

    async def _failure(
        self, claim: JobClaim, reason: str, *, error_type: str | None = None
    ) -> dict[str, JsonValue] | None:
        observe("memory_consolidation_total", labels={"outcome": "ERROR"})
        logger.warning(
            "memory_consolidation_failed",
            extra={
                "tenant_id": claim.tenant_id,
                "job_id": str(claim.id),
                "reason": reason,
                "error_type": error_type,
            },
        )
        try:
            return await self.fail(claim, reason=reason)
        except SQLAlchemyError as exc:
            logger.warning(
                "memory_consolidation_failure_record_failed",
                extra={"job_id": str(claim.id), "error_type": type(exc).__name__},
            )
            observe("memory_consolidation_total", labels={"outcome": "ERROR"})
            return None

    async def fail(self, claim: JobClaim, *, reason: str) -> dict[str, JsonValue] | None:
        async with self.factory.begin() as session:
            job = await lock_claim(session, claim)
            if job is None:
                return await terminal_result(session, claim)
            return await fail_locked(session, job, reason=reason)
