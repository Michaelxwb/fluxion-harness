"""Restore typed execution counters and event input from the frozen Run source."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.failure import FailureSignal, update_failure_state
from muad_agent_core.agent.recovery import NOT_EXECUTED_CODE, fold_events, replay_failure_state
from muad_agent_core.agent.termination import FailureState
from muad_agent_core.tools.outcome import FAILURE_STATUSES, BusinessStatus
from muad_contracts import AsyncToolPolicy
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.async_tools import RunContinuation
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord, RuntimeSnapshot
from .async_tools.continuation_service import ExecutionIdentity
from .async_tools.event_port import DurableRuntimeEventPort


def checkpoint_of(row: RunContinuation | None) -> RunnerCheckpoint:
    if row is None:
        return RunnerCheckpoint()
    if row.checkpoint_schema_version != 1 or row.phase != "BEFORE_MODEL":
        raise ValueError("unsupported continuation checkpoint")
    return RunnerCheckpoint(
        row.turns, row.tool_calls, row.input_tokens, row.output_tokens, row.consumed_event_seq
    )


def _recover_window_limit(
    checkpoint: RunnerCheckpoint, completed: Sequence[Mapping[str, Any]], *, text: str,
) -> RunnerCheckpoint:
    facts = [
        (item, outcome)
        for item in completed
        if isinstance(outcome := item.get("outcome"), Mapping)
    ]
    if not any(
        outcome.get("business_status") == BusinessStatus.RATE_LIMITED
        and outcome.get("error_code") == "LOCAL_RATE_LIMITED"
        for _, outcome in facts
    ):
        return checkpoint

    failure = FailureState.from_payload(checkpoint.failure_state)
    for item, outcome in facts:
        if (
            outcome.get("business_status") in FAILURE_STATUSES
            and outcome.get("retryable") is not True
            and outcome.get("error_code") not in {NOT_EXECUTED_CODE, "TOOL_BUDGET_EXHAUSTED"}
        ):
            failure = update_failure_state(
                failure,
                FailureSignal(
                    tool=str(item["tool_name"]),
                    tool_call_id=str(item["tool_call_id"]),
                    business_status=str(outcome["business_status"]),
                    failure_layer=str(outcome.get("failure_layer", "NONE")),
                    error_code=outcome.get("error_code"),
                    exit_code=outcome.get("exit_code"),
                    evidence_preview=str(outcome.get("evidence_preview") or ""),
                ),
                max_identical_failures=1,
            )
    return replace(
        checkpoint, recovered_window_limit=True, recovered_window_text=text,
        failure_state=failure.as_payload(),
    )


async def _latest_completed_tools(
    session: AsyncSession, run: RunRecord,
) -> tuple[str, list[dict[str, Any]]]:
    event = CanonicalEvent
    scope = (
        event.tenant_id == run.tenant_id,
        event.conversation_id == run.conversation_id,
        event.run_id == run.id,
        event.is_deleted.is_(False),
    )
    last_turn = select(func.max(event.seq)).where(*scope, event.event_type == "ASSISTANT_TURN")
    rows = await session.execute(
        select(event.event_type, event.payload_json)
        .where(
            *scope,
            or_(event.event_type == "ASSISTANT_TURN",
                and_(event.event_type == "TOOL_CALL", event.seq > last_turn.scalar_subquery())),
        )
        .order_by(event.seq)
    )
    text: list[str] = []
    completed: list[dict[str, Any]] = []
    for kind, payload in rows:
        if not payload:
            continue
        if kind == "ASSISTANT_TURN":
            text.append(str(payload.get("text") or ""))
        elif payload.get("state") == "COMPLETED":
            completed.append(payload)
    return "".join(text), completed


async def _replay_state(
    session: AsyncSession, run: RunRecord, policy: Mapping[str, Any],
) -> FailureState:
    rows = await session.execute(
        select(CanonicalEvent.event_type, CanonicalEvent.payload_json)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.conversation_id == run.conversation_id,
            CanonicalEvent.run_id == run.id,
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.event_type.in_(["ASSISTANT_TURN", "TOOL_CALL"]),
        )
        .order_by(CanonicalEvent.seq)
    )
    fold = fold_events([{"event_type": kind, "payload": payload} for kind, payload in rows])
    raw_limit = policy.get("max_identical_failures")
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 2
    return replay_failure_state(fold, max_identical_failures=limit)


async def _previous_unverified_external(session: AsyncSession, run: RunRecord) -> bool:
    event, done = CanonicalEvent, aliased(CanonicalEvent)
    prior_completed_safely = (
        select(done.id)
        .where(
            done.tenant_id == event.tenant_id,
            done.conversation_id == event.conversation_id,
            done.run_id == event.run_id,
            done.event_type == "TOOL_CALL",
            done.is_deleted.is_(False),
            done.payload_json["state"].as_string() == "COMPLETED",
            done.payload_json["tool_call_id"].as_string() == event.payload_json["tool_call_id"].as_string(),
            func.coalesce(done.payload_json["outcome"]["failure_layer"].as_string(), "NONE") == "NONE",
        )
        .exists()
    )
    return bool(await session.scalar(select(
        select(event.id).where(
            event.tenant_id == run.tenant_id,
            event.conversation_id == run.conversation_id,
            event.run_id != run.id,
            event.is_deleted.is_(False),
            event.event_type == "TOOL_CALL",
            event.payload_json["state"].as_string() == "STARTED",
            event.payload_json["effect"].as_string().in_(["WRITE", "EXTERNAL"]),
            event.payload_json["retry_safety"].as_string() != "READ_ONLY",
            ~prior_completed_safely,
        ).exists()
    )))


async def execution_checkpoint(
    factory: SessionFactory, run: RunRecord
) -> tuple[RunnerCheckpoint, DurableRuntimeEventPort, AsyncToolPolicy]:
    async with factory() as session:
        saved = await session.scalar(
            select(RunContinuation).where(
                RunContinuation.run_id == run.id,
                RunContinuation.tenant_id == run.tenant_id,
                RunContinuation.is_deleted.is_(False),
            )
        )
        snapshot = (
            await session.scalars(
                select(RuntimeSnapshot).prefix_with("/* checkpoint */").where(
                    RuntimeSnapshot.id == run.snapshot_id,
                    RuntimeSnapshot.tenant_id == run.tenant_id,
                    RuntimeSnapshot.is_deleted.is_(False),
                )
            )
        ).one()
        checkpoint = checkpoint_of(saved)
        state = (
            await _replay_state(session, run, snapshot.policy_json)
            if run.execution_epoch > 1 else FailureState()
        )
        if await _previous_unverified_external(session, run):
            state = replace(state, external_blocked=True)
        checkpoint = replace(checkpoint, failure_state=state.as_payload())
        if run.execution_epoch > 1:
            text, completed = await _latest_completed_tools(session, run)
            checkpoint = _recover_window_limit(checkpoint, completed, text=text)
    policy = AsyncToolPolicy.model_validate(snapshot.policy_json.get("async_tools", {}))
    return (
        checkpoint,
        DurableRuntimeEventPort(factory, ExecutionIdentity.of(run)),
        policy,
    )
