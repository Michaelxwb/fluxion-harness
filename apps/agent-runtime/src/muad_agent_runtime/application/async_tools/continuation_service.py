"""Conversation-first transactions for complete-round checkpoints and waiting claims."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from muad_agent_core.agent.continuation import RunnerCheckpoint, WaitDecision
from muad_agent_core.agent.runner import RunnerCancelled
from muad_contracts import CompletionMode, OperationStatus, RunStatus
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ...infrastructure.db import SessionFactory
from ...infrastructure.models.async_tools import RunContinuation, ToolOperation, ToolResultInbox
from ...infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
    RunSubmission,
    RuntimeSnapshot,
)
from ..run_events import EventWriter
from ..tool_result_receipts import OP_TERMINAL, lock_run_source

CONSUMABLE_TYPES = ("TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT", "TOOL_SUBMISSION_FAILED")
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class ExecutionLeaseLost(RunnerCancelled):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    tenant_id: str
    run_id: UUID
    owner: str
    epoch: int

    @classmethod
    def of(cls, run: RunRecord) -> "ExecutionIdentity":
        if run.lease_owner is None:
            raise ExecutionLeaseLost("Run has no execution owner")
        return cls(run.tenant_id, run.id, run.lease_owner, run.execution_epoch)


def ensure_execution(run: RunRecord, identity: ExecutionIdentity, now: datetime) -> None:
    if (
        run.tenant_id != identity.tenant_id
        or run.id != identity.run_id
        or run.status != RunStatus.RUNNING
        or run.lease_owner != identity.owner
        or run.execution_epoch != identity.epoch
        or run.lease_until is None
        or run.lease_until <= now
        or (run.deadline_at is not None and run.deadline_at <= now)
    ):
        raise ExecutionLeaseLost("execution owner, epoch, lease or absolute deadline rejected")


async def lock_execution(
    session: AsyncSession, identity: ExecutionIdentity, now: datetime
) -> tuple[RunRecord, RunContinuation | None]:
    run = await session.scalar(
        select(RunRecord).where(
            RunRecord.id == identity.run_id,
            RunRecord.tenant_id == identity.tenant_id,
            RunRecord.is_deleted.is_(False),
        )
    )
    if run is None:
        raise ExecutionLeaseLost("execution source missing")
    run, checkpoint = await lock_run_source(session, identity.tenant_id, run)
    ensure_execution(run, identity, now)
    return run, checkpoint


async def _write_checkpoint(
    session: AsyncSession,
    run: RunRecord,
    saved: RunContinuation | None,
    progress: RunnerCheckpoint,
    now: datetime,
) -> RunContinuation:
    await _ensure_complete_round(session, run)
    if saved is None:
        if run.snapshot_id is None:
            raise ValueError("checkpoint requires a frozen snapshot")
        saved = RunContinuation(
            tenant_id=run.tenant_id,
            run_id=run.id,
            snapshot_id=run.snapshot_id,
            context_upto_seq=0,
            consumed_event_seq=0,
            turns=0,
            tool_calls=0,
            wait_generation=0,
            runner_state_json={},
        )
        session.add(saved)
    if (
        progress.turns < saved.turns
        or progress.tool_calls < saved.tool_calls
        or progress.consumed_event_seq < saved.consumed_event_seq
    ):
        raise ValueError("checkpoint cannot reset persisted counters or consumption cursor")
    saved.context_upto_seq = int(
        await session.scalar(
            select(func.max(CanonicalEvent.seq)).where(
                CanonicalEvent.tenant_id == run.tenant_id,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
        or 0
    )
    saved.turns, saved.tool_calls = progress.turns, progress.tool_calls
    saved.input_tokens, saved.output_tokens = progress.input_tokens, progress.output_tokens
    saved.consumed_event_seq, saved.update_time = progress.consumed_event_seq, now
    saved.runner_state_json = JSON_OBJECT.validate_python(
        {"schema_version": 1, "phase": "BEFORE_MODEL", **asdict(progress)}
    )
    await session.flush()
    await _complete_consumed(session, run, progress.consumed_event_seq, now)
    return saved


async def _ensure_complete_round(session: AsyncSession, run: RunRecord) -> None:
    declared = await session.scalar(select(CanonicalEvent).where(CanonicalEvent.tenant_id == run.tenant_id,
        CanonicalEvent.run_id == run.id, CanonicalEvent.event_type == "ASSISTANT_TURN",
        CanonicalEvent.is_deleted.is_(False)).order_by(CanonicalEvent.seq.desc()).limit(1))
    if declared is None:
        return
    payload = JSON_OBJECT.validate_python(declared.payload_json)
    raw = payload.get("tool_calls")
    if not isinstance(raw, list):
        raise ValueError("checkpoint requires a complete tool round")
    ids = {call["id"] for call in raw if isinstance(call, dict) and isinstance(call.get("id"), str)}
    responses = tuple(
        await session.scalars(
            select(CanonicalEvent).where(
                CanonicalEvent.tenant_id == run.tenant_id,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.event_type == "TOOL_CALL",
                CanonicalEvent.seq > declared.seq,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
    )
    answered = {str(row.payload_json.get("tool_call_id")) for row in responses}
    if len(ids) != len(raw) or not ids <= answered:
        raise ValueError("checkpoint requires a complete tool round")


async def _complete_consumed(session: AsyncSession, run: RunRecord, cursor: int, now: datetime) -> None:
    consumed = (
        select(ToolResultInbox.operation_id)
        .join(CanonicalEvent, CanonicalEvent.id == ToolResultInbox.canonical_event_id)
        .where(
            ToolResultInbox.tenant_id == run.tenant_id,
            ToolResultInbox.run_id == run.id,
            ToolResultInbox.materialized.is_(True),
            ToolResultInbox.late.is_(False),
            ToolResultInbox.is_deleted.is_(False),
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.seq <= cursor,
        )
    )
    ops = list(
        await session.scalars(
            select(ToolOperation)
            .where(
                ToolOperation.id.in_(consumed),
                ToolOperation.tenant_id == run.tenant_id,
                ToolOperation.is_deleted.is_(False),
                ToolOperation.status == OperationStatus.MATERIALIZED,
            )
            .order_by(ToolOperation.id)
            .with_for_update()
        )
    )
    for op in ops:
        op.status, op.update_time = OperationStatus.COMPLETED, now


async def checkpoint_execution(
    factory: SessionFactory,
    identity: ExecutionIdentity,
    progress: RunnerCheckpoint,
    *,
    now: datetime | None = None,
) -> None:
    instant = now or datetime.now(UTC)
    async with factory() as session, session.begin():
        run, saved = await lock_execution(session, identity, instant)
        await _write_checkpoint(session, run, saved, progress, instant)


async def has_ready(session: AsyncSession, run: RunRecord, cursor: int) -> bool:
    unmaterialized = await session.scalar(
        select(ToolResultInbox.id)
        .where(
            ToolResultInbox.tenant_id == run.tenant_id,
            ToolResultInbox.run_id == run.id,
            ToolResultInbox.materialized.is_(False),
            ToolResultInbox.late.is_(False),
            ToolResultInbox.is_deleted.is_(False),
        )
        .limit(1)
    )
    if unmaterialized is not None:
        return True
    event = await session.scalar(
        select(CanonicalEvent.id)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.run_id == run.id,
            CanonicalEvent.event_type.in_(CONSUMABLE_TYPES),
            CanonicalEvent.seq > cursor,
            CanonicalEvent.is_deleted.is_(False),
        )
        .limit(1)
    )
    return event is not None


async def _has_dependency(session: AsyncSession, run: RunRecord) -> bool:
    ops = list(
        await session.scalars(
            select(ToolOperation)
            .where(
                ToolOperation.tenant_id == run.tenant_id,
                ToolOperation.run_id == run.id,
                ToolOperation.is_deleted.is_(False),
            )
            .order_by(ToolOperation.id)
            .with_for_update()
        )
    )
    return any(
        op.status == OperationStatus.SUBMIT_PENDING
        or (op.completion_mode == CompletionMode.JOIN and op.status not in OP_TERMINAL)
        for op in ops
    )


async def try_wait(
    factory: SessionFactory,
    identity: ExecutionIdentity,
    progress: RunnerCheckpoint,
    *,
    assistant_text: str = "",
    now: datetime | None = None,
) -> WaitDecision:
    instant = now or datetime.now(UTC)
    async with factory() as session, session.begin():
        run, saved = await lock_execution(session, identity, instant)
        saved = await _write_checkpoint(session, run, saved, progress, instant)
        pending = await _has_dependency(session, run)
        saved.ready = await has_ready(session, run, progress.consumed_event_seq)
        if saved.ready:
            return WaitDecision.CONTINUE
        if not pending:
            return WaitDecision.FINISH
        saved.wait_generation += 1
        saved.phase = "BEFORE_MODEL"
        writer = EventWriter(session)
        if assistant_text:
            await writer.append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="ASSISTANT_MESSAGE",
                payload={"text": assistant_text, "intermediate": True},
            )
        saved.context_upto_seq = await writer.append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_WAITING_TOOL",
            stream_type="run.waiting_tool",
            payload={
                "status": "WAITING_TOOL",
                "wait_generation": saved.wait_generation,
                "intermediate_text": assistant_text,
            },
        )
        run.status, run.lease_owner, run.lease_until, run.update_time = (
            RunStatus.WAITING_TOOL,
            None,
            None,
            instant,
        )
    return WaitDecision.WAIT


async def renew_execution(
    factory: SessionFactory, identity: ExecutionIdentity, *, lease_sec: int, now: datetime | None = None
) -> bool:
    instant = now or datetime.now(UTC)
    async with factory() as session, session.begin():
        changed = await session.scalar(
            update(RunRecord)
            .where(
                RunRecord.id == identity.run_id,
                RunRecord.tenant_id == identity.tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status == RunStatus.RUNNING,
                RunRecord.lease_owner == identity.owner,
                RunRecord.execution_epoch == identity.epoch,
                RunRecord.lease_until > instant,
                (RunRecord.deadline_at.is_(None) | (RunRecord.deadline_at > instant)),
            )
            .values(
                lease_until=instant + timedelta(seconds=lease_sec), heartbeat_at=instant, update_time=instant
            )
            .returning(RunRecord.id)
        )
    return changed is not None


async def claim_continuation(
    factory: SessionFactory,
    *,
    instance_id: str,
    lease_sec: int,
    wait_generation: int | None = None,
    now: datetime | None = None,
    exclude_runs: frozenset[UUID] = frozenset(),
    run_id: UUID | None = None,
) -> RunRecord | None:
    instant = now or datetime.now(UTC)
    async with factory() as session:
        candidates = list(
            await session.scalars(
                select(RunRecord)
                .outerjoin(RunContinuation, RunContinuation.run_id == RunRecord.id)
                .where(
                    _claimable_predicate(),
                    RunRecord.cancel_requested.is_(False),
                    RunRecord.is_deleted.is_(False),
                    RunRecord.id.not_in(exclude_runs),
                    (RunRecord.deadline_at.is_(None) | (RunRecord.deadline_at > instant)),
                    RunRecord.id == run_id if run_id is not None else true(),
                )
                .order_by(RunRecord.update_time, RunRecord.id)
                .limit(128)
            )
        )
    for candidate in candidates:
        claimed = await _claim_one(factory, candidate, instance_id, lease_sec, wait_generation, instant)
        if claimed is not None:
            return claimed
    return None


def _claimable_predicate() -> ColumnElement[bool]:
    frozen = select(RuntimeSnapshot.id).where(
        RuntimeSnapshot.id == RunRecord.snapshot_id,
        RuntimeSnapshot.tenant_id == RunRecord.tenant_id,
        RuntimeSnapshot.is_deleted.is_(False),
    ).exists()
    submitted = select(RunSubmission.id).where(
        RunSubmission.run_id == RunRecord.id,
        RunSubmission.tenant_id == RunRecord.tenant_id,
        RunSubmission.status == "OPEN", RunSubmission.is_deleted.is_(False),
    ).exists()
    waiting = (RunRecord.status == RunStatus.WAITING_TOOL) & (
        RunContinuation.tenant_id == RunRecord.tenant_id
    ) & RunContinuation.is_deleted.is_(False) & _ready_predicate()
    return frozen & (waiting | ((RunRecord.status == RunStatus.CREATED) & submitted))


def _ready_predicate() -> ColumnElement[bool]:
    inbox = select(ToolResultInbox.id).where(
        ToolResultInbox.run_id == RunRecord.id,
        ToolResultInbox.tenant_id == RunRecord.tenant_id,
        ToolResultInbox.materialized.is_(False),
        ToolResultInbox.late.is_(False),
        ToolResultInbox.is_deleted.is_(False),
    ).exists()
    events = select(CanonicalEvent.id).where(
        CanonicalEvent.run_id == RunRecord.id,
        CanonicalEvent.tenant_id == RunRecord.tenant_id,
        CanonicalEvent.event_type.in_(CONSUMABLE_TYPES),
        CanonicalEvent.seq > RunContinuation.consumed_event_seq,
        CanonicalEvent.is_deleted.is_(False),
    ).exists()
    return inbox | events


async def _claim_one(
    factory: SessionFactory,
    candidate: RunRecord,
    owner: str,
    lease_sec: int,
    generation: int | None,
    now: datetime,
) -> RunRecord | None:
    async with factory() as session, session.begin():
        conversation = await session.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == candidate.conversation_id,
                Conversation.tenant_id == candidate.tenant_id,
                Conversation.is_deleted.is_(False),
            )
            .with_for_update(skip_locked=True)
        )
        if conversation is None:
            return None
        run, saved = await lock_run_source(session, candidate.tenant_id, candidate)
        starting = run.status == RunStatus.CREATED
        if run.cancel_requested or (not starting and (saved is None or run.status != RunStatus.WAITING_TOOL)):
            return None
        if (run.deadline_at is not None and run.deadline_at <= now) or (
            generation is not None and (saved is None or saved.wait_generation != generation)
        ):
            return None
        if not starting and (saved is None or not await has_ready(session, run, saved.consumed_event_seq)):
            return None
        run.status, run.execution_epoch = RunStatus.RUNNING, run.execution_epoch + 1
        run.lease_owner, run.lease_until = owner, now + timedelta(seconds=lease_sec)
        run.heartbeat_at, run.update_time = now, now
        if saved is not None:
            saved.ready = False
        if not starting:
            await EventWriter(session).append(
                tenant_id=run.tenant_id, conversation_id=run.conversation_id, run_id=run.id,
                event_type="RUN_RESUMED", stream_type="run.resumed",
                payload={"execution_epoch": run.execution_epoch,
                    "wait_generation": saved.wait_generation if saved is not None else 0},
            )
        await session.flush()
    return run
