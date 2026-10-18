"""Durable job leases; every mutation fences the post-lock database clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from muad_contracts.memory import memory_hash
from pydantic import JsonValue
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import Conversation, MemoryConsolidationJob, RunRecord, RuntimeSnapshot
from .memory_commands import MemoryWriteContext
from .memory_consolidation_input import ConsolidationInput, ConsolidationStale, build_input
from .memory_job_enqueue import enqueue_completed as enqueue_completed
from .memory_observability import after_commit
from .memory_service import MemoryService
from .run_service import execution_defaults_of


@dataclass(frozen=True, slots=True)
class JobClaim:
    id: UUID
    tenant_id: str
    run_id: UUID
    conversation_id: UUID
    owner: str
    epoch: int
    exhausted: bool = False


def _eligible(now: datetime) -> ColumnElement[bool]:
    predecessor = aliased(MemoryConsolidationJob)
    return and_(
        MemoryConsolidationJob.is_deleted.is_(False),
        or_(
            and_(
                MemoryConsolidationJob.status.in_(("PENDING", "RETRY")),
                MemoryConsolidationJob.next_attempt_at <= now,
            ),
            and_(MemoryConsolidationJob.status == "RUNNING", MemoryConsolidationJob.lease_until <= now),
        ),
        ~exists(
            select(predecessor.id).where(
                predecessor.tenant_id == MemoryConsolidationJob.tenant_id,
                predecessor.conversation_id == MemoryConsolidationJob.conversation_id,
                predecessor.through_seq < MemoryConsolidationJob.through_seq,
                predecessor.status.in_(("PENDING", "RUNNING", "RETRY")),
                predecessor.is_deleted.is_(False),
            )
        ),
    )


async def lock_claim(session: AsyncSession, claim: JobClaim) -> MemoryConsolidationJob | None:
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == claim.conversation_id,
            Conversation.tenant_id == claim.tenant_id,
            Conversation.is_deleted.is_(False),
        )
        .with_for_update()
    )
    run = await session.scalar(
        select(RunRecord)
        .where(
            RunRecord.id == claim.run_id,
            RunRecord.tenant_id == claim.tenant_id,
            RunRecord.conversation_id == claim.conversation_id,
            RunRecord.is_deleted.is_(False),
        )
        .with_for_update()
    )
    job = await session.scalar(
        select(MemoryConsolidationJob)
        .where(
            MemoryConsolidationJob.id == claim.id,
            MemoryConsolidationJob.tenant_id == claim.tenant_id,
            MemoryConsolidationJob.run_id == claim.run_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation is None or run is None or job is None or run.status != "COMPLETED":
        return None
    if conversation.user_id != job.user_id or run.user_id != job.user_id:
        return None
    return job if owns(job, claim, await database_now(session)) else None


def owns(job: MemoryConsolidationJob, claim: JobClaim, now: datetime) -> bool:
    return (
        job.status == "RUNNING"
        and job.lease_owner == claim.owner
        and job.claim_epoch == claim.epoch
        and job.lease_until is not None
        and job.lease_until > now
        and not job.is_deleted
    )


class MemoryJobs:
    def __init__(self, factory: SessionFactory) -> None:
        self.factory = factory

    async def claim(self, owner: str, *, exclude: frozenset[UUID] = frozenset()) -> JobClaim | None:
        if not owner or len(owner) > 128:
            raise ValueError("invalid job lease owner")
        async with self.factory.begin() as session:
            now = await database_now(session)
            job = await session.scalar(
                select(MemoryConsolidationJob)
                .where(_eligible(now), MemoryConsolidationJob.id.not_in(exclude))
                .order_by(MemoryConsolidationJob.create_time, MemoryConsolidationJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            snapshot = await session.get(RuntimeSnapshot, job.snapshot_id)
            if snapshot is None:
                raise ValueError("source snapshot unavailable")
            policy = execution_defaults_of(snapshot.policy_json).memory_policy
            job.claim_epoch += 1
            exhausted = job.attempt >= policy.consolidation_max_attempts
            if not exhausted:
                job.attempt += 1
            job.status, job.lease_owner = "RUNNING", owner
            job.lease_until = await database_now(session) + timedelta(seconds=policy.consolidation_lease_sec)
            after_commit(session, "memory_consolidation_total", labels={"outcome": "CLAIMED"})
            return JobClaim(
                job.id, job.tenant_id, job.run_id, job.conversation_id, owner, job.claim_epoch, exhausted
            )

    async def freeze_input(
        self, claim: JobClaim, *, credential_canaries: tuple[str, ...]
    ) -> dict[str, JsonValue] | None:
        async with self.factory.begin() as session:
            job = await lock_claim(session, claim)
            if job is None:
                return None
            snapshot = await session.get(RuntimeSnapshot, job.snapshot_id)
            if snapshot is None:
                raise ValueError("source snapshot unavailable")
            policy = execution_defaults_of(snapshot.policy_json).memory_policy
            scope = await MemoryService.lock_scope(
                session,
                MemoryWriteContext(
                    tenant_id=job.tenant_id, user_id=job.user_id, actor_type="SYSTEM", actor_id=claim.owner
                ),
            )
            if not owns(job, claim, await database_now(session)):
                return None
            expected = job.input_scope_revision if job.input_json is not None else job.enqueued_scope_revision
            if scope.revision != expected:
                raise ConsolidationStale()
            if job.input_json is not None:
                ConsolidationInput.model_validate(job.input_json)
                if job.input_hash != memory_hash(job.input_json):
                    raise ValueError("fixed input hash mismatch")
                return job.input_json
            payload = await build_input(
                session,
                job,
                credential_canaries=credential_canaries,
                max_input_bytes=policy.consolidation_max_input_bytes,
            )
            if not owns(job, claim, await database_now(session)):
                return None
            job.input_json = payload.model_dump(mode="json")
            job.input_hash, job.input_scope_revision = (
                memory_hash(job.input_json),
                payload.input_scope_revision,
            )
            return job.input_json

    async def set_failure(self, claim: JobClaim, *, reason: str) -> bool:
        if reason not in {"FAILED", "STALE", "INPUT_TOO_LARGE", "REJECTED", "DISABLED"}:
            raise ValueError("invalid job failure reason")
        async with self.factory.begin() as session:
            job = await lock_claim(session, claim)
            if job is None:
                return False
            from .memory_job_effects import fail_locked

            await fail_locked(session, job, reason=reason)
            return True
