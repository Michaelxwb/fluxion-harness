"""Short, parent-first commit transaction for one complete consolidation batch."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from muad_contracts import ResolvedAgent
from muad_contracts.memory import (
    ExperienceCandidate,
    MemoryCategory,
    MemoryRetirementReason,
    MemorySourceEvidence,
    memory_hash,
    task_memory_key,
)
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryConsolidationJob,
    RunRecord,
    RuntimeSnapshot,
    UserMemory,
)
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_consolidation_input import ConsolidationInput
from .memory_consolidation_output import ConsolidationBatch, ConsolidationRejected, parse_output
from .memory_identity import memory_key as build_memory_key
from .memory_job_enqueue import _reference
from .memory_jobs import JobClaim, lock_claim, owns
from .memory_mutations import memory_state
from .memory_observability import after_commit
from .memory_service import MemoryService
from .memory_tools import memory_write_enabled
from .memory_write import decide_automatic_update
from .run_events import EventWriter
from .run_service import execution_defaults_of


class ConsolidationFenced(RuntimeError):
    """Ownership expired while writes were being flushed; roll back the whole round."""


async def terminal_result(session: AsyncSession, claim: JobClaim) -> dict[str, JsonValue] | None:
    job = await session.scalar(
        select(MemoryConsolidationJob).where(
            MemoryConsolidationJob.id == claim.id,
            MemoryConsolidationJob.tenant_id == claim.tenant_id,
            MemoryConsolidationJob.run_id == claim.run_id,
            MemoryConsolidationJob.conversation_id == claim.conversation_id,
        )
    )
    if job is None or job.status not in {"DONE", "SKIPPED", "FAILED"} or not job.result_json:
        return None
    return job.result_json[0]


async def finish_job(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    *,
    status: str,
    reason: str,
    written: int = 0,
    skipped: int = 0,
    candidates: int = 0,
) -> dict[str, JsonValue]:
    if status not in {"DONE", "SKIPPED", "FAILED"} or reason not in {
        "NONE",
        "NO_CANDIDATES",
        "POLICY",
        "STALE",
        "INPUT_TOO_LARGE",
        "REJECTED",
        "FAILED",
        "DISABLED",
    }:
        raise ValueError("invalid consolidation terminal result")
    result: dict[str, JsonValue] = {
        "written": written,
        "skipped": skipped,
        "candidates": candidates,
        "reason": reason,
        "attempt": job.attempt,
        "through_seq": job.through_seq,
    }
    await EventWriter(session).append(
        tenant_id=job.tenant_id,
        conversation_id=job.conversation_id,
        run_id=job.run_id,
        event_type="MEMORY_CONSOLIDATED",
        payload=result,
    )
    job.status, job.error_code, job.result_json = status, None if reason == "NONE" else reason, [result]
    job.lease_owner, job.lease_until = None, None
    after_commit(session, "memory_consolidation_total", labels={"outcome": status})
    await session.flush()
    return result


async def apply_batch(
    session: AsyncSession, claim: JobClaim, batch: ConsolidationBatch, *, credential_canaries: tuple[str, ...]
) -> dict[str, JsonValue] | None:
    job = await lock_claim(session, claim)
    if job is None:
        return await terminal_result(session, claim)
    snapshot = await session.scalar(
        select(RuntimeSnapshot)
        .prefix_with("/* memory_effects */")
        .where(RuntimeSnapshot.id == job.snapshot_id)
    )
    if snapshot is None:
        raise ValueError("source snapshot unavailable")
    ctx = await _context(session, job, snapshot, credential_canaries)
    scope = await MemoryService.lock_scope(session, ctx)
    if not owns(job, claim, await database_now(session)):
        return None
    if not ctx.settings.write_enabled:
        return await finish_job(session, job, status="SKIPPED", reason="DISABLED")
    if job.input_json is None or job.input_hash != memory_hash(job.input_json):
        raise ConsolidationRejected()
    payload = ConsolidationInput.model_validate(job.input_json)
    if (payload.through_seq, payload.input_scope_revision) != (job.through_seq, job.input_scope_revision):
        raise ConsolidationRejected()
    if scope.revision != job.input_scope_revision:
        return await finish_job(
            session, job, status="SKIPPED", reason="STALE", candidates=len(batch.candidates)
        )
    root = payload.resume_ref.root_conversation_id if payload.resume_ref else job.conversation_id
    checked, rejected = parse_output(
        batch.model_dump_json(),
        finish_reason="stop",
        conversation_id=job.conversation_id,
        root_conversation_id=root,
        max_candidates=ctx.settings.consolidation_max_candidates,
        credential_canaries=credential_canaries,
    )
    mutations = await _task_mutations(session, job, payload, checked, ctx)
    return await _write(session, job, claim, ctx, checked, mutations, rejected)


async def _context(
    session: AsyncSession, job: MemoryConsolidationJob, snapshot: RuntimeSnapshot, canaries: tuple[str, ...]
) -> MemoryWriteContext:
    if (snapshot.tenant_id, snapshot.run_id, snapshot.id) != (job.tenant_id, job.run_id, job.snapshot_id):
        raise ConsolidationRejected()
    run = await session.get(RunRecord, job.run_id)
    if run is None or run.snapshot_id != snapshot.id:
        raise ConsolidationRejected()
    reference = await _reference(session, run, job.through_seq)
    if (reference.model_dump(mode="json") if reference else None) != job.resume_ref_json:
        raise ConsolidationRejected()
    policy = execution_defaults_of(snapshot.policy_json).memory_policy
    agent = ResolvedAgent.model_validate(snapshot.agent_json)
    policy = replace(
        policy, write_enabled=memory_write_enabled(agent.runtime_config, default=policy.write_enabled)
    )
    event = await session.scalar(
        select(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == job.tenant_id,
            CanonicalEvent.conversation_id == job.conversation_id,
            CanonicalEvent.run_id == job.run_id,
            CanonicalEvent.seq <= job.through_seq,
            CanonicalEvent.event_type == "USER_MESSAGE",
            CanonicalEvent.is_deleted.is_(False),
        )
        .order_by(CanonicalEvent.seq.desc())
        .limit(1)
    )
    revision = event.payload_json.get("source_scope_revision") if event else None
    if event is None or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ConsolidationRejected()
    return MemoryWriteContext(
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        actor_type="SYSTEM",
        actor_id="memory-consolidation",
        source_run_id=job.run_id,
        conversation_id=job.conversation_id,
        settings=policy,
        credential_canaries=canaries,
        evidence=MemorySourceEvidence(
            source_event_id=event.id, source_seq=event.seq, source_scope_revision=revision
        ),
    )


async def _task_mutations(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    payload: ConsolidationInput,
    batch: ConsolidationBatch,
    ctx: MemoryWriteContext,
) -> list[MemoryMutation]:
    """TASK_STATE keeps its own lane; facts and notes are v2 candidates applied by the writer."""

    root_key = payload.resume_ref.memory_key if payload.resume_ref else task_memory_key(job.conversation_id)
    row = await session.scalar(
        select(UserMemory).where(
            UserMemory.tenant_id == job.tenant_id,
            UserMemory.user_id == job.user_id,
            UserMemory.memory_key == root_key,
        )
    )
    frozen = payload.resume_ref.expected_version if payload.resume_ref else None
    mutations: list[MemoryMutation] = []
    if batch.task_state is not None:
        mutations.append(
            MemoryMutation(
                action="UPSERT",
                candidate=ExperienceCandidate(
                    memory_key=root_key,
                    category=MemoryCategory.TASK_STATE,
                    content=batch.task_state,
                ),
                expected_version=frozen if frozen is not None else (row.version if row is not None else None),
            )
        )
    if batch.task_completed:
        expected = frozen if frozen is not None else (row.version if row is not None else None)
        retirement = _completion(row, expected, ctx)
        if retirement is not None:
            mutations.append(retirement)
    return mutations


async def _apply_facts(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    ctx: MemoryWriteContext,
    batch: ConsolidationBatch,
) -> tuple[int, int]:
    """Apply v2 candidates through the single writer; conflicts become PENDING candidates."""

    written = skipped = 0
    service = MemoryService()
    for index, item in enumerate(batch.candidates):
        candidate = item.normalized(taxonomy_version=1)
        if await _duplicate_fact(session, job, candidate):
            skipped += 1
            continue
        outcome = await service.apply_candidate_in_session(
            session,
            ctx,
            candidate,
            command_id=f"consolidation:{job.id}:{index}",
            endpoint="memory.consolidation",
        )
        if outcome.status in {"CREATED", "UPDATED"}:
            written += 1
        else:
            skipped += 1
    return written, skipped


async def _duplicate_fact(
    session: AsyncSession, job: MemoryConsolidationJob, candidate
) -> bool:
    """Same content on the same identity is not a new change; NOTE has no stable key."""

    content = dict(candidate.content)
    if candidate.memory_kind == "NOTE":
        found = await session.scalar(
            select(UserMemory.id).where(
                UserMemory.tenant_id == job.tenant_id,
                UserMemory.user_id == job.user_id,
                UserMemory.memory_kind == "NOTE",
                UserMemory.predicate == candidate.predicate,
                UserMemory.content_json == content,
                UserMemory.is_deleted.is_(False),
            )
        )
        return found is not None
    key = build_memory_key(candidate)
    row = await session.scalar(
        select(UserMemory).where(
            UserMemory.tenant_id == job.tenant_id,
            UserMemory.user_id == job.user_id,
            UserMemory.memory_key == key,
        )
    )
    return row is not None and not row.is_deleted and row.content_json == content


def _completion(
    row: UserMemory | None, expected: JsonValue, ctx: MemoryWriteContext
) -> MemoryMutation | None:
    if row is None or isinstance(expected, bool) or not isinstance(expected, int):
        return None
    if expected != row.version or row.is_deleted:
        return None
    assert ctx.source_run_id is not None
    decision = decide_automatic_update(
        memory_state(row),
        expected_version=expected,
        evidence=ctx.evidence,
        source_run_id=ctx.source_run_id,
        same_conversation=row.source_ref == f"conversation:{ctx.conversation_id}",
    )
    if not decision.allowed:
        return None
    return MemoryMutation(
        action="RETIRE",
        memory_key=row.memory_key,
        expected_version=expected,
        retirement_reason=MemoryRetirementReason.TASK_COMPLETE,
    )


async def _write(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    claim: JobClaim,
    ctx: MemoryWriteContext,
    batch: ConsolidationBatch,
    mutations: list[MemoryMutation],
    rejected: int,
) -> dict[str, JsonValue]:
    total = len(batch.candidates) + (1 if batch.task_state is not None else 0)
    if not mutations and not batch.candidates:
        return await finish_job(
            session,
            job,
            status="SKIPPED",
            reason="POLICY" if batch.task_completed else "NO_CANDIDATES",
            skipped=rejected,
            candidates=total,
        )
    written, skipped = await _apply_facts(session, job, ctx, batch)
    skipped += rejected
    if mutations:
        outcome = await MemoryService().execute_in_session(
            session,
            ctx,
            MemoryCommand(
                command_id=f"consolidation:{job.id}",
                endpoint="memory.consolidation",
                expected_scope_revision=job.input_scope_revision,
                mutations=mutations,
            ),
        )
        written += sum(
            item.status in {"CREATED", "UPDATED", "RESTORED", "RETIRED"}
            for item in outcome.response.items
        )
        skipped += sum(item.status == "SKIPPED" for item in outcome.response.items)
    if not owns(job, claim, await database_now(session)):
        raise ConsolidationFenced()
    return await finish_job(
        session,
        job,
        status="DONE" if written else "SKIPPED",
        reason="NONE" if written else "POLICY",
        written=written,
        skipped=skipped,
        candidates=total,
    )


async def fail_locked(
    session: AsyncSession, job: MemoryConsolidationJob, *, reason: str
) -> dict[str, JsonValue] | None:
    snapshot = await session.get(RuntimeSnapshot, job.snapshot_id)
    if snapshot is None:
        raise ConsolidationRejected()
    policy = execution_defaults_of(snapshot.policy_json).memory_policy
    if reason == "FAILED" and job.attempt < policy.consolidation_max_attempts:
        job.status, job.error_code, job.lease_owner, job.lease_until = "RETRY", reason, None, None
        job.next_attempt_at = await database_now(session) + timedelta(
            seconds=policy.consolidation_retry_delay_sec
        )
        after_commit(session, "memory_consolidation_total", labels={"outcome": "RETRY"})
        return None
    return await finish_job(session, job, status="FAILED" if reason == "FAILED" else "SKIPPED", reason=reason)
