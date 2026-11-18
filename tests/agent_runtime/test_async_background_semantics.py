"""TASK-006: Run-side ASYNC is always a background task; only JOIN waits for results."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from muad_agent_core.agent.continuation import RunnerCheckpoint, WaitDecision
from muad_agent_runtime.application.async_tools.continuation_service import (
    claim_continuation,
    has_ready,
    try_wait,
)
from muad_agent_runtime.application.async_tools.operations import (
    ACTIVE_OPERATION_STATUSES,
    _capacity,
    cancel_operations,
    reserve_operation,
)
from muad_agent_runtime.application.skill_tools import (
    EXECUTE_SKILL_TOOL,
    EXECUTE_TOOL,
    SkillToolSet,
)
from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation
from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    AsyncToolPolicy,
    CompletionMode,
    OperationStatus,
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    RunStatus,
    SkillExecutionMode,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.async_tool_continuations import operation, receipt, running_source
from tests.async_tool_submissions import seed_submission

pytestmark = pytest.mark.integration


def _async_skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid4(),
        artifact_id=uuid4(),
        key="async-report",
        name="Async report",
        description="",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/async-report.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        source="ADMIN",
    )


def _context(skill: ResolvedSkill) -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id="tenant-background",
        actor_user_id=uuid4(),
        agent=ResolvedAgent(
            id=uuid4(), key="agent", revision=1, instructions="", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid4(), revision=1, model_id="probe", base_url="http://model.invalid", params={}
        ),
        skills=(skill,),
        source_run_id=uuid4(),
    )


class _RecordingSubmitter:
    def __init__(self) -> None:
        self.modes: list[CompletionMode] = []

    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data,
        call_id: str,
        completion_mode: CompletionMode,
        retry_safety=None,
        argv=(),
    ):
        self.modes.append(completion_mode)
        return {
            "status": "SUBMITTED",
            "operation_id": str(uuid4()),
            "task_id": str(uuid4()),
            "task_status": "QUEUED",
            "completion_mode": completion_mode.value,
        }


class _RejectingSubmitter:
    async def submit_task(self, *args, **kwargs):
        raise AppError(ErrorCode.TOOL_OPERATION_CAPACITY_EXCEEDED)


def _tool_set(tmp_path, skill, submitter) -> SkillToolSet:
    cache = SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache")
    return SkillToolSet(
        cache=cache,
        skills=(skill,),
        timeout_sec=30,
        task_client=submitter,
        task_context=_context(skill),
    )


def test_run_side_tools_do_not_expose_completion_mode(tmp_path) -> None:
    skill = _async_skill()
    tool_set = _tool_set(tmp_path, skill, _RecordingSubmitter())
    registry = tool_set.registry()
    execute = registry.get(EXECUTE_TOOL)
    legacy = registry.get(EXECUTE_SKILL_TOOL)

    assert "completion_mode" not in execute.input_schema["properties"]
    assert "completion_mode" not in legacy.input_schema["properties"]
    assert "background" in execute.description.lower()
    assert "task center" in execute.description.lower()


async def test_legacy_execute_skill_submits_detach(tmp_path) -> None:
    skill = _async_skill()
    submitter = _RecordingSubmitter()
    legacy = _tool_set(tmp_path, skill, submitter).registry().get(EXECUTE_SKILL_TOOL)

    payload = json.loads(
        await legacy.handler({"skill_key": skill.key, "input": {}}, call_id="legacy-call")
    )

    assert payload["status"] == "SUBMITTED"
    assert submitter.modes == [CompletionMode.DETACH]


async def test_unified_execute_rejects_completion_mode(tmp_path) -> None:
    skill = _async_skill()
    submitter = _RecordingSubmitter()
    execute = _tool_set(tmp_path, skill, submitter).registry().get(EXECUTE_TOOL)

    payload = json.loads(
        await execute.handler(
            {
                "target": "skill",
                "skill_key": skill.key,
                "input": {},
                "completion_mode": "JOIN",
            },
            call_id="mixed-mode",
        )
    )

    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
    assert submitter.modes == [], "被拒绝的调用不得产生后台任务"


async def test_unaccepted_submission_is_an_explicit_failure(tmp_path) -> None:
    skill = _async_skill()
    execute = _tool_set(tmp_path, skill, _RejectingSubmitter()).registry().get(EXECUTE_TOOL)

    payload = json.loads(
        await execute.handler(
            {"target": "skill", "skill_key": skill.key, "input": {}}, call_id="rejected"
        )
    )

    assert payload["error"]["code"] == "BACKGROUND_SUBMIT_FAILED"
    assert "TOOL_OPERATION_CAPACITY_EXCEEDED" in payload["error"]["message"]
    assert "task_id" not in payload


@pytest.mark.parametrize("status", ACTIVE_OPERATION_STATUSES)
async def test_every_active_operation_occupies_capacity(
    async_tool_database, status: OperationStatus
) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    context, skill, run = await seed_submission(factory, pending_limit=10)
    op = await reserve_operation(
        factory,
        context,
        skill=skill,
        input_data={},
        call_id=f"active-{status.value}",
        completion_mode=CompletionMode.DETACH,
    )
    async with factory() as session, session.begin():
        await session.execute(
            update(ToolOperation).where(ToolOperation.id == op.id).values(status=status)
        )
    async with factory() as session:
        with pytest.raises(AppError) as error:
            await _capacity(session, run, AsyncToolPolicy(pending_operation_limit=1))
    assert error.value.code == ErrorCode.TOOL_OPERATION_CAPACITY_EXCEEDED


@pytest.mark.parametrize(
    "status",
    [
        OperationStatus.RESULT_RECEIVED,
        OperationStatus.MATERIALIZED,
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.LATE,
    ],
)
async def test_non_active_operation_does_not_occupy_capacity(
    async_tool_database, status: OperationStatus
) -> None:
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    context, skill, run = await seed_submission(factory, pending_limit=10)
    op = await reserve_operation(
        factory,
        context,
        skill=skill,
        input_data={},
        call_id=f"inactive-{status.value}",
        completion_mode=CompletionMode.JOIN,
    )
    async with factory() as session, session.begin():
        await session.execute(
            update(ToolOperation).where(ToolOperation.id == op.id).values(status=status)
        )
    async with factory() as session:
        await _capacity(session, run, AsyncToolPolicy(pending_operation_limit=1))


async def test_detach_result_does_not_wake_a_join_waiter(async_tool_database) -> None:
    factory, context, skill, run, identity = await running_source(async_tool_database)
    join_op = await operation(factory, context, skill, call="join", mode=CompletionMode.JOIN)
    detach_op = await operation(
        factory, context, skill, call="detach", mode=CompletionMode.DETACH
    )
    await receipt(factory, run, detach_op, {"detach": "first"})

    async with factory() as session:
        assert await has_ready(session, run, 0) is False, "DETACH 结果不参与等待就绪"
    assert await try_wait(factory, identity, RunnerCheckpoint()) is WaitDecision.WAIT
    assert await claim_continuation(factory, instance_id="detach-only", lease_sec=30) is None

    await receipt(factory, run, join_op, {"join": "ready"})
    claimed = await claim_continuation(factory, instance_id="join-ready", lease_sec=30)
    assert claimed is not None and claimed.id == run.id


async def test_detach_only_turn_finishes_without_waiting_tool(async_tool_database) -> None:
    factory, context, skill, run, identity = await running_source(async_tool_database)
    await operation(factory, context, skill, call="detach-only", mode=CompletionMode.DETACH)

    assert await try_wait(factory, identity, RunnerCheckpoint()) is WaitDecision.FINISH
    async with factory() as session:
        stored = await session.get(RunRecord, run.id)
        assert stored is not None and stored.status is RunStatus.RUNNING


async def test_run_cancel_does_not_cancel_detached_operations(async_tool_database) -> None:
    factory, context, skill, run, _ = await running_source(async_tool_database)
    detach_op = await operation(factory, context, skill, call="detach", mode=CompletionMode.DETACH)
    join_op = await operation(factory, context, skill, call="join", mode=CompletionMode.JOIN)
    async with factory() as session, session.begin():
        await session.execute(
            update(ToolOperation)
            .where(ToolOperation.id.in_((detach_op.id, join_op.id)))
            .values(status=OperationStatus.SUBMITTED)
        )
        await cancel_operations(session, run)
    async with factory() as session:
        rows = {
            row.id: row
            for row in await session.scalars(
                select(ToolOperation).where(ToolOperation.id.in_((detach_op.id, join_op.id)))
            )
        }
    assert rows[detach_op.id].status is OperationStatus.SUBMITTED, "DETACH 后台任务独立执行"
    assert rows[join_op.id].status is OperationStatus.CANCELLED, "JOIN 仍随 Run 取消"
