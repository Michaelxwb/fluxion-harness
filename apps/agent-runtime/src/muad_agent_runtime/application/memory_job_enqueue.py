"""A failed optional enqueue rolls back its savepoint, never a completed Run."""

import logging

from muad_common import SharedSettings
from muad_contracts import ResolvedAgent
from muad_contracts.memory import MemoryResumeRef
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryConsolidationJob,
    MemoryScope,
    RunRecord,
    RuntimeSnapshot,
)
from .memory_observability import after_commit, observe
from .memory_resume_queries import _decision
from .memory_tools import memory_write_enabled
from .run_service import execution_defaults_of

logger = logging.getLogger(__name__)


async def enqueue_completed(session: AsyncSession, run: RunRecord, *, through_seq: int) -> bool:
    identity = {"tenant_id": run.tenant_id, "run_id": str(run.id)}
    if run.status != "COMPLETED":
        return False
    try:
        async with session.begin_nested():
            added = await _insert(session, run, through_seq)
            if added:
                after_commit(session, "memory_consolidation_enqueue_total", labels={"outcome": "OK"})
        return added
    except (SQLAlchemyError, ValueError, ValidationError) as exc:
        observe("memory_consolidation_enqueue_total", labels={"outcome": "ERROR"})
        logger.warning("memory_job_enqueue_failed", extra=identity | {"error_type": type(exc).__name__})
        return False


async def _insert(session: AsyncSession, run: RunRecord, through_seq: int) -> bool:
    # 队列上限是**进程级**容量护栏：到界后不再为新的完成 Run 入队（不阻塞终态提交），
    # 已有 job 继续由 MemorySupervisor 有界消费；积压不会无限增长。
    pending = await session.scalar(
        select(func.count())
        .select_from(MemoryConsolidationJob)
        .where(
            MemoryConsolidationJob.tenant_id == run.tenant_id,
            MemoryConsolidationJob.is_deleted.is_(False),
            MemoryConsolidationJob.status.in_(("PENDING", "RUNNING", "RETRY")),
        )
    )
    if int(pending or 0) >= SharedSettings().memory_consolidation_queue_max:
        observe("memory_consolidation_enqueue_total", labels={"outcome": "QUEUE_FULL"})
        return False
    snapshot = await session.scalar(
        select(RuntimeSnapshot)
        .prefix_with("/* memory_enqueue */")
        .where(RuntimeSnapshot.id == run.snapshot_id)
    )
    if snapshot is None or (snapshot.tenant_id, snapshot.run_id) != (run.tenant_id, run.id):
        raise ValueError("source snapshot unavailable")
    policy = execution_defaults_of(snapshot.policy_json).memory_policy
    agent = ResolvedAgent.model_validate(snapshot.agent_json)
    if not policy.consolidation_enabled or not memory_write_enabled(
        agent.runtime_config, default=policy.write_enabled
    ):
        return False
    count = await session.scalar(
        select(func.count())
        .select_from(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.conversation_id == run.conversation_id,
            CanonicalEvent.seq <= through_seq,
            CanonicalEvent.event_type == "USER_MESSAGE",
            CanonicalEvent.is_deleted.is_(False),
        )
    )
    if count is None or count < policy.consolidation_min_user_messages:
        return False
    revision = await session.scalar(
        select(MemoryScope.revision).where(
            MemoryScope.tenant_id == run.tenant_id, MemoryScope.user_id == run.user_id
        )
    )
    reference = await _reference(session, run, through_seq)
    result = await session.execute(
        insert(MemoryConsolidationJob)
        .values(
            tenant_id=run.tenant_id,
            run_id=run.id,
            snapshot_id=snapshot.id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            through_seq=through_seq,
            enqueued_scope_revision=revision or 0,
            resume_ref_json=reference.model_dump(mode="json") if reference else None,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "run_id"])
        .returning(MemoryConsolidationJob.id)
    )
    return result.scalar_one_or_none() is not None


async def _reference(session: AsyncSession, run: RunRecord, through_seq: int) -> MemoryResumeRef | None:
    events = list(
        await session.scalars(
            select(CanonicalEvent)
            .where(
                CanonicalEvent.tenant_id == run.tenant_id,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type.in_(
                    ("MEMORY_RESUME_RESOLVED", "MEMORY_RESUME_OPTIONS", "MEMORY_RESUME_NOTICE")
                ),
                CanonicalEvent.seq <= through_seq,
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq.desc())
        )
    )
    return _decision(events, run.id).reference
