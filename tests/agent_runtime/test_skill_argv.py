"""Public Skill tools pass CLI flags literally, independently of JSON stdin."""

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from muad_agent_runtime.application.async_tools.operations import _input_hash
from muad_agent_runtime.application.skill_tools import SkillToolSet
from muad_agent_runtime.application.task_client import WorkerTaskClient, build_task_snapshot
from muad_contracts import CompletionMode, SkillExecutionMode, snapshot_hash

from agent_runtime.test_skill_tools import SKILL_MD, _call, _env_for
from agent_runtime.test_task_handoff import _submission_context

CLI_SCRIPT = (
    "import argparse, json, sys\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--company-id', action='append', required=True)\n"
    "p.add_argument('--start')\n"
    "p.add_argument('--notify-no-event', action='store_true')\n"
    "print(json.dumps({'args': vars(p.parse_args()), 'stdin': json.load(sys.stdin)}))\n"
)
ARGV = ["--company-id", "123", "--company-id", "456", "--start", "2026-11-07 10:00", "--notify-no-event"]


@pytest.mark.parametrize("tool", ["execute_skill", "run_skill_script"])
async def test_tools_run_unmodified_cli_scripts(tmp_path: Path, tool: str) -> None:
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": CLI_SCRIPT})
    arguments = {"skill_key": env.skill.key, "argv": ARGV, "input": {"unrelated": "stdin"}}
    if tool == "run_skill_script":
        arguments["script"] = "main.py"
    result = await _call(env.registry, tool, arguments)
    assert result["status"] == "SUCCEEDED", result
    assert result["result"] == {
        "args": {"company_id": ["123", "456"], "start": "2026-11-07 10:00", "notify_no_event": True},
        "stdin": {"unrelated": "stdin"},
    }


@pytest.mark.parametrize("tool", ["execute_skill", "run_skill_script"])
@pytest.mark.parametrize("argv", [None, "--company-id 123", [123], ["bad\0argument"]])
async def test_tool_rejects_invalid_argv_before_execution(tmp_path: Path, tool: str, argv: object) -> None:
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": "raise RuntimeError('executed')"})
    result = await _call(env.registry, tool, {"skill_key": env.skill.key, "script": "main.py", "argv": argv})
    assert result["error"]["code"] == "COMMON_VALIDATION_ERROR"


async def test_background_tool_freezes_argv_separately_from_stdin(tmp_path: Path) -> None:
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": CLI_SCRIPT})
    skill = env.skill.model_copy(update={"execution_mode": SkillExecutionMode.ASYNC})
    context = _submission_context(actor_user_id=uuid4(), skill=skill, delivery_route=None)
    submitter = AsyncMock()
    submitter.submit_task.return_value = {"status": "SUBMISSION_PENDING"}
    tools = SkillToolSet(cache=env.cache, skills=[skill], timeout_sec=10,
                         task_client=submitter, task_context=context)
    await tools.execute_skill({"skill_key": skill.key, "argv": ARGV, "input": {"x": 1}}, call_id="cli")
    submitted = submitter.submit_task.call_args.kwargs
    assert submitted["argv"] == tuple(ARGV)
    assert submitted["input_data"] == {"x": 1}
    snapshot, digest = build_task_snapshot(context, skill, argv=submitted["argv"])
    assert snapshot["argv"] == ARGV
    assert digest == snapshot_hash(snapshot)
    changed, changed_digest = build_task_snapshot(context, skill, argv=("--company-id", "789"))
    assert changed_digest != digest
    assert changed["argv"] != snapshot["argv"]
    assert _input_hash(skill, {}, CompletionMode.JOIN, argv=tuple(ARGV)) != _input_hash(
        skill, {}, CompletionMode.JOIN, argv=("--company-id", "789")
    )


async def test_worker_client_transmits_argv_in_snapshot(tmp_path: Path) -> None:
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": CLI_SCRIPT})
    context = _submission_context(actor_user_id=uuid4(), skill=env.skill, delivery_route=None)
    received = []

    def handle(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"code": "0", "data": {"status": "QUEUED"}})

    client = WorkerTaskClient("http://worker", transport=httpx.MockTransport(handle))
    try:
        await client.submit_task(
            context, skill=env.skill, input_data={"x": 1}, call_id="cli", argv=tuple(ARGV)
        )
    finally:
        await client.aclose()
    assert received[0]["input"] == {"x": 1}
    assert received[0]["execution_snapshot"]["argv"] == ARGV
    assert received[0]["snapshot_hash"] == snapshot_hash(received[0]["execution_snapshot"])
