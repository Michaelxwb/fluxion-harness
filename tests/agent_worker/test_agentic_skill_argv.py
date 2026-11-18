"""Skill tools carry stdin input and CLI argv as two separate contracts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.skill import SkillExecutionRequest, SkillExecutionResult, SkillExecutionStatus
from muad_agent_worker.application.agentic.snapshot import load_frozen_execution
from muad_agent_worker.application.agentic.suspension import AgenticSuspension
from muad_agent_worker.application.agentic.tools import build_registry
from muad_agent_worker.worker.agentic_skills import AgenticSkillTools
from muad_contracts import (
    CompletionMode,
    OperationStatus,
    SnapshotSkill,
    TaskToolOperationOwner,
    TaskToolOperationView,
)

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_agentic_adapter import (
    ARTIFACT_ID,
    CHECKSUM,
    SKILL_ID,
    STAGING_TOOL,
    agentic_snapshot,
    insert_task,
)

pytestmark = pytest.mark.integration


def _skill(*, execution_mode: str = "SYNC") -> SnapshotSkill:
    return SnapshotSkill(
        skill_id=SKILL_ID,
        artifact_id=ARTIFACT_ID,
        version="1.0.0",
        checksum=CHECKSUM,
        storage_key="skills/report.zip",
        source="ADMIN",
        execution_mode=execution_mode,
        frontmatter={"key": "report", "name": "报告", "description": "生成报告"},
    )


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], tuple[str, ...], str]] = []

    async def call(
        self,
        skill: SnapshotSkill,
        tool_name: str,
        input: dict[str, Any],
        argv: tuple[str, ...],
        *,
        call_id: str,
    ) -> str:
        self.calls.append((tool_name, dict(input), tuple(argv), call_id))
        return "ok"


async def test_skill_tool_schema_separates_input_from_argv(tenant: TenantContext) -> None:
    snapshot = agentic_snapshot()
    snapshot["skills"][0]["execution_mode"] = "SYNC"
    snapshot["tools"][0]["execution_mode"] = "SYNC"
    task = await insert_task(tenant, snapshot)
    frozen = load_frozen_execution(task)
    runner = _RecordingRunner()

    registry = build_registry(frozen, mcp=None, skills=runner)  # type: ignore[arg-type]
    definition = registry.registry.get(STAGING_TOOL)
    schema = dict(definition.input_schema)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["input"]["properties"] == {"q": {"type": "string"}}
    assert schema["properties"]["argv"]["items"] == {"type": "string"}
    assert definition.handler is not None
    result = await definition.handler(
        {"input": {"q": "本周"}, "argv": ["--date", "2026-03-07"]}, call_id="call-1"
    )

    assert result == "ok"
    assert runner.calls == [("deliver_report", {"q": "本周"}, ("--date", "2026-03-07"), "call-1")]


async def test_invalid_argv_or_input_never_reaches_the_skill(tenant: TenantContext) -> None:
    snapshot = agentic_snapshot()
    snapshot["skills"][0]["execution_mode"] = "SYNC"
    snapshot["tools"][0]["execution_mode"] = "SYNC"
    task = await insert_task(tenant, snapshot)
    frozen = load_frozen_execution(task)
    runner = _RecordingRunner()
    definition = build_registry(frozen, mcp=None, skills=runner).registry.get(STAGING_TOOL)  # type: ignore[arg-type]
    assert definition.handler is not None

    bad_argv = json.loads(await definition.handler({"input": {}, "argv": [1]}, call_id="call-1"))
    assert bad_argv["status"] == "INVALID"
    bad_input = json.loads(await definition.handler({"input": "not-an-object"}, call_id="call-2"))
    assert bad_input["status"] == "INVALID"
    assert runner.calls == []


@dataclass
class _CapturingExecutor:
    request: SkillExecutionRequest | None = None

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        self.request = request
        return SkillExecutionResult(status=SkillExecutionStatus.SUCCEEDED, stdout="ok")


@dataclass
class _Resolver:
    root: Path

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self.root


async def test_sync_skill_request_passes_input_and_argv_separately(
    tenant: TenantContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muad_agent_worker.worker import agentic_skills

    async def passthrough(executor: Any, request: SkillExecutionRequest, **_: Any) -> Any:
        return await executor.execute(request)

    monkeypatch.setattr(agentic_skills, "execute_task_skill_with_egress", passthrough)
    task = await insert_task(tenant, agentic_snapshot())
    executor = _CapturingExecutor()
    tools = AgenticSkillTools(
        task=task,
        resolver=_Resolver(tmp_path),  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        settings=tenant.settings,
        sessions=None,
        suspension=AgenticSuspension(execution_id=task.id),
        operations=None,
        workspace_store=None,
        create_async_child=False,
    )

    result = await tools.call(
        _skill(), "run", {"company_id": "22921947"}, ("--date", "2026-03-07"), call_id="call-1"
    )

    assert result == "ok"
    assert executor.request is not None
    assert executor.request.input == {"company_id": "22921947"}
    assert executor.request.argv == ("--date", "2026-03-07")
    assert "argv" not in executor.request.input


@dataclass
class _RecordingOperations:
    submits: list[dict[str, Any]] = field(default_factory=list)
    operation_id: uuid.UUID = field(default_factory=uuid.uuid4)

    async def submit(self, **kwargs: Any) -> TaskToolOperationView:
        self.submits.append(kwargs)
        return TaskToolOperationView(
            operation_id=self.operation_id,
            owner=TaskToolOperationOwner(task_execution_id=kwargs["execution_id"]),
            source_tool_call_id=kwargs["call_id"],
            tool_name=kwargs["tool_name"],
            completion_mode=CompletionMode.JOIN,
            status=OperationStatus.SUBMITTED,
            cancel_requested=False,
            result=None,
        )


async def test_async_child_task_keeps_input_in_stdin_and_argv_in_snapshot(
    tenant: TenantContext, tmp_path: Path
) -> None:
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from sqlalchemy import select

    task = await insert_task(tenant, agentic_snapshot())
    operations = _RecordingOperations()
    tools = AgenticSkillTools(
        task=task,
        resolver=_Resolver(tmp_path),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        settings=tenant.settings,
        sessions=tenant.session_factory,
        suspension=AgenticSuspension(execution_id=task.id),
        operations=operations,  # type: ignore[arg-type]
        workspace_store=None,
        create_async_child=True,
    )

    await tools.call(
        _skill(execution_mode="ASYNC"),
        "run",
        {"company_id": "22921947"},
        ("--date", "2026-03-07"),
        call_id="call-1",
    )

    assert operations.submits[0]["arguments"] == {
        "input": {"company_id": "22921947"},
        "argv": ["--date", "2026-03-07"],
    }
    async with tenant.session_factory() as session:
        child = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.source_operation_id == operations.operation_id
            )
        )
    assert child is not None
    assert child.task_type == "SKILL"
    assert child.input_json == {"company_id": "22921947"}
    assert child.execution_snapshot_json["argv"] == ["--date", "2026-03-07"]
