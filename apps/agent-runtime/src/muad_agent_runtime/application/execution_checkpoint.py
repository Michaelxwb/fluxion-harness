"""Restore typed execution counters and event input from the frozen Run source."""

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_contracts import AsyncToolPolicy
from sqlalchemy import select

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.async_tools import RunContinuation
from ..infrastructure.models.runtime import RunRecord, RuntimeSnapshot
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
                select(RuntimeSnapshot).where(
                    RuntimeSnapshot.id == run.snapshot_id,
                    RuntimeSnapshot.tenant_id == run.tenant_id,
                    RuntimeSnapshot.is_deleted.is_(False),
                )
            )
        ).one()
    policy = AsyncToolPolicy.model_validate(snapshot.policy_json.get("async_tools", {}))
    return (
        checkpoint_of(saved),
        DurableRuntimeEventPort(factory, ExecutionIdentity.of(run)),
        policy,
    )
