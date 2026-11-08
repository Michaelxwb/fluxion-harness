"""Worker replays frozen CLI arguments through a real Skill subprocess."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime.test_skill_tools import SKILL_MD, _env_for
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError, frozen_argv


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
        external_ref_json=None, deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        execution_snapshot_json={"argv": ["--company-id", "123", "--company-id", "456"], "skills": [{
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
