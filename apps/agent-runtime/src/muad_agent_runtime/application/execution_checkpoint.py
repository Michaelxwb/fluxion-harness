"""Restore typed execution counters and event input from the frozen Run source."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.failure import FailureSignal, update_failure_state
from muad_agent_core.agent.recovery import NOT_EXECUTED_CODE
from muad_agent_core.agent.termination import FailureState
from muad_agent_core.tools.outcome import FAILURE_STATUSES, BusinessStatus
from muad_contracts import AsyncToolPolicy
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        if run.execution_epoch > 1:
            text, completed = await _latest_completed_tools(session, run)
            checkpoint = _recover_window_limit(checkpoint, completed, text=text)
    policy = AsyncToolPolicy.model_validate(snapshot.policy_json.get("async_tools", {}))
    return (
        checkpoint,
        DurableRuntimeEventPort(factory, ExecutionIdentity.of(run)),
        policy,
    )
