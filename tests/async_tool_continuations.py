"""Frozen Run, a live execution identity and real durable result receipts."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from muad_contracts import CompletionMode, TerminalStatus, ToolResultRequest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_submissions import seed_submission


async def running_source(engine, *, limit=4):
    from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
    from muad_agent_runtime.infrastructure.models.runtime import RunRecord

    factory = async_sessionmaker(engine, expire_on_commit=False)
    context, skill, run = await seed_submission(factory, pending_limit=limit)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run.id)
        row.lease_owner = "original"
        row.lease_until = datetime.now(UTC) + timedelta(minutes=2)
        row.execution_epoch = 1
    identity = ExecutionIdentity(run.tenant_id, run.id, "original", 1)
    return factory, context, skill, run, identity


async def operation(factory, context, skill, *, call="call-1", mode=CompletionMode.JOIN):
    from muad_agent_runtime.application.async_tools.operations import reserve_operation

    return await reserve_operation(
        factory, context, skill=skill, input_data={}, call_id=call, completion_mode=mode
    )


async def receipt(factory, run, op, result):
    from muad_agent_runtime.application.tool_result_receipts import receive_result

    request = ToolResultRequest(
        event_id=uuid4(),
        operation_id=op.id,
        task_id=uuid4(),
        source_run_id=run.id,
        actor_user_id=run.user_id,
        task_snapshot_hash=op.task_snapshot_hash,
        task_event_seq=1,
        terminal_status=TerminalStatus.COMPLETED,
        result=result,
        completed_at=datetime.now(UTC),
    )
    async with factory() as session, session.begin():
        await receive_result(session, run.tenant_id, request)
    return request
