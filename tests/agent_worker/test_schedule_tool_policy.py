"""TASK-018：per-job 工具面只收紧，不越权；平台只读工具按策略注册。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from muad_agent_worker.application.agentic.tools import ScheduledPlatformTools, build_registry
from muad_agent_worker.application.agentic.turn_result import ResultRecorder
from muad_contracts import SnapshotAgent, SnapshotGrants, SnapshotModel
from muad_contracts.scheduling import TaskExecutionSnapshotV4


def _frozen(*, tools_allow: tuple[str, ...] = ()):
    snapshot = TaskExecutionSnapshotV4(
        mode="AGENTIC",
        agent=SnapshotAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="hi"),
        model=SnapshotModel(id=uuid.uuid4(), revision=1),
        grants=SnapshotGrants(agent_access=True),
        allowlist=[],
        budget={},
    )
    task = SimpleNamespace(
        trigger_type="SCHEDULED",
        input_json={"tools_allow": list(tools_allow)} if tools_allow else {},
        deadline_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    return SimpleNamespace(
        snapshot=snapshot,
        task=task,
        scheduled=True,
        tools_allow=tools_allow,
        deadline_at=task.deadline_at,
    )


class _FakeTaskQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def list_tasks(self, *, limit: int):
        self.calls.append(("list", limit))
        return [{"task_id": "t-1", "status": "COMPLETED", "summary": "ok"}]

    async def get_task(self, task_id):
        self.calls.append(("get", task_id))
        return {"task_id": str(task_id), "status": "COMPLETED", "summary": "ok"}


def _registry(tools_allow: tuple[str, ...] = (), *, task_query=None):
    platform = ScheduledPlatformTools(
        zone=ZoneInfo("Asia/Shanghai"),
        result_recorder=ResultRecorder(),
        task_query=task_query,
    )
    return build_registry(_frozen(tools_allow=tools_allow), mcp=None, skills=None, platform=platform)


def test_default_tool_policy_follows_owner_and_registers_read_only_tools() -> None:
    names = {definition.name for definition in _registry().registry.list()}

    assert "current_time" in names
    assert "record_result" in names
    assert "ask_user" not in names


def test_explicit_policy_cannot_add_current_time_when_omitted() -> None:
    names = {
        definition.name
        for definition in _registry(("SKILL:" + str(uuid.uuid4()) + ":run",)).registry.list()
    }

    assert "current_time" not in names
    assert "record_result" in names
    assert "ask_user" not in names


async def test_task_query_tools_are_owner_scoped_and_bounded() -> None:
    query = _FakeTaskQuery()
    registry = _registry(task_query=query)
    names = {definition.name for definition in registry.registry.list()}

    assert {"list_tasks", "get_task"} <= names
    listed = registry.registry.get("list_tasks")
    fetched = registry.registry.get("get_task")
    assert listed.handler is not None and fetched.handler is not None
    payload = json.loads(await listed.handler({"limit": 99}, call_id="call-list"))
    assert payload["tasks"][0]["task_id"] == "t-1"
    assert query.calls[-1] == ("list", 20)

    task_id = uuid.uuid4()
    found = json.loads(await fetched.handler({"task_id": str(task_id)}, call_id="call-get"))
    assert found["status"] == "FOUND"
    assert query.calls[-1] == ("get", task_id)
