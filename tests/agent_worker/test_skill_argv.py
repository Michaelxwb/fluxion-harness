"""Worker replays frozen CLI arguments through a real Skill subprocess."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime.test_skill_tools import SKILL_MD, _env_for
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import (
    SkillTaskExecutor,
    TaskExecutionError,
    frozen_argv,
    frozen_skill_input,
)
from muad_contracts.platform_settings import default_platform_settings

#: Snapshot budget must freeze the per-execution timeout (v3); fixtures use the schema default.
_TIMEOUT_SEC = default_platform_settings().task.execution_timeout_sec
_FIRE_ENVELOPE = {
    "kind": "SKILL_PLAN",
    "mode": "SKILL",
    "session_target": "isolated",
    "skill_id": str(uuid4()),
    "input": {"company_id": "22921947", "report_day": "2026-03-07"},
    "argv": ["--company-id", "22921947"],
    "window": {"kind": "DAY", "offset": -1},
    "context": {},
}


async def test_worker_uses_frozen_argv_without_changing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py":
        "import argparse, json, sys\n"
        "p=argparse.ArgumentParser(); p.add_argument('--company-id', action='append', required=True)\n"
        "print(json.dumps({'companies':p.parse_args().company_id, 'stdin':json.load(sys.stdin)}))\n"})
    task = TaskExecution(
        id=uuid4(), tenant_id="cli-test", actor_user_id=uuid4(), agent_id=uuid4(), task_type="SKILL",
        skill_artifact_id=env.skill.artifact_id, input_json={"x": 1}, idempotency_key="cli", attempt=1,
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        execution_snapshot_json={"argv": ["--company-id", "123", "--company-id", "456"],
                                 "budget": {"execution_timeout_sec": _TIMEOUT_SEC}, "skills": [{
            "artifact_id": str(env.skill.artifact_id), "checksum": env.skill.checksum,
            "storage_key": env.skill.storage_key, "source": "ADMIN", "platforms": [],
            "workspace": {"tenant_id": "cli-test", "user_id": str(uuid4()), "agent_id": str(uuid4())},
        }]},
    )
    result = await SkillTaskExecutor(env.cache).execute(task)
    assert result["status"] == "SUCCEEDED", result
    assert result["result"] == {"companies": ["123", "456"], "stdin": {"x": 1}}


@pytest.mark.parametrize("value", [None, "--company-id 123", [1], ["bad\0argument"]])
def test_malformed_frozen_argv_is_not_retryable(value: object) -> None:
    task = TaskExecution(execution_snapshot_json={"argv": value})
    with pytest.raises(TaskExecutionError) as error:
        frozen_argv(task)
    assert error.value.retryable is False


def test_tasks_without_cli_arguments_keep_empty_argv() -> None:
    assert frozen_argv(TaskExecution(execution_snapshot_json={})) == ()


def test_fire_envelope_only_sends_business_input_to_stdin() -> None:
    """定时 Fire 的信封只把业务 input 交给 stdin。"""
    task = TaskExecution(trigger_type="SCHEDULED", input_json=dict(_FIRE_ENVELOPE))

    assert frozen_skill_input(task) == {"company_id": "22921947", "report_day": "2026-03-07"}


def test_immediate_task_sends_the_input_itself_to_stdin() -> None:
    task = TaskExecution(trigger_type="IMMEDIATE", input_json={"customers": ["A"]})

    assert frozen_skill_input(task) == {"customers": ["A"]}


def test_old_scheduled_envelope_without_kind_only_sends_input_to_stdin() -> None:
    task = TaskExecution(
        trigger_type="SCHEDULED",
        input_json={"mode": "SKILL", "input": {"report_day": "2026-03-07"}, "context": {}},
    )

    assert frozen_skill_input(task) == {"report_day": "2026-03-07"}


def test_scheduled_task_sends_only_the_nested_input_to_stdin() -> None:
    task = TaskExecution(trigger_type="SCHEDULED", input_json={"input": {"x": 1}})

    assert frozen_skill_input(task) == {"x": 1}


def test_old_scheduled_task_without_input_key_gets_empty_stdin() -> None:
    task = TaskExecution(trigger_type="SCHEDULED", input_json={"mode": "SKILL", "context": {}})

    assert frozen_skill_input(task) == {}


def test_envelope_with_non_object_input_is_not_retryable() -> None:
    task = TaskExecution(
        trigger_type="SCHEDULED",
        input_json={"kind": "SKILL_PLAN", "input": "--company-id 123"},
    )

    with pytest.raises(TaskExecutionError) as error:
        frozen_skill_input(task)
    assert error.value.code == "SKILL_SNAPSHOT_INVALID"
    assert error.value.retryable is False
