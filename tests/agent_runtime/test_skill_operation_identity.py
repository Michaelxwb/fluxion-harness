"""Runtime tool-call identity and real dev-unsafe output isolation.

This is not the PG/Redis recovery acceptance scenario S-03.
"""
import json

import pytest
from muad_agent_runtime.application.skill_tools import SkillToolSet, _workspace_execution_id

from agent_runtime.test_skill_tools import SKILL_MD, _env_for

SCRIPT = (
    "import json, os\nfrom pathlib import Path\n"
    "out = Path(os.environ['SKILL_OUTPUT_DIR'])\n"
    "before = sorted(p.name for p in out.iterdir())\n"
    "(out / 'result.txt').write_text('completed')\n"
    "print(json.dumps({'before': before}))\n"
)


@pytest.mark.parametrize("first,second", [("call/a", "call?a"), ("x" * 100 + "a", "x" * 100 + "b")])
async def test_distinct_tool_calls_never_share_previous_script_outputs(tmp_path, first, second):
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": SCRIPT})
    handler = env.registry.get("execute").handler
    for call_id in (first, second):
        payload = json.loads(await handler({"target": "skill", "skill_key": env.skill.key}, call_id=call_id))
        assert payload.get("status") == "SUCCEEDED", payload
        assert payload["result"]["before"] == [], "different calls inherited prior outputs"


def test_full_run_identity_participates_in_output_directory():
    first = "12345678-0000-4000-8000-000000000001"
    second = "12345678-0000-4000-8000-000000000002"
    assert _workspace_execution_id(first, "call") != _workspace_execution_id(second, "call")


@pytest.mark.parametrize("call_id", ["fixed/" + "long-" * 30, "固定值", "opaque\0id"])
async def test_runtime_forwards_logical_call_key_separately_from_workspace(tmp_path, monkeypatch, call_id):
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": SCRIPT})
    original, observed = SkillToolSet._run_executor, []
    async def capture(self, *args, **kwargs):
        observed.append((kwargs.get("invocation_id"), kwargs["execution_id"]))
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(SkillToolSet, "_run_executor", capture)
    payload = json.loads(await env.registry.get("execute").handler(
        {"target": "skill", "skill_key": env.skill.key}, call_id=call_id,
    ))
    assert payload.get("status") == "SUCCEEDED", payload
    key, directory = observed[0]
    assert key is not None, "root execution did not forward a stable logical key"
    assert json.loads(key) == ["TOOL_CALL", call_id]
    assert directory != key
    assert len(directory) <= 64 and directory.isascii()


def test_workspace_key_is_stable_for_same_persisted_call():
    assert _workspace_execution_id("run", "固定值") == _workspace_execution_id("run", "固定值")


@pytest.mark.parametrize("call_id", ["", "  "])
async def test_missing_call_identity_is_rejected_before_business(tmp_path, call_id):
    env = _env_for(tmp_path, {"SKILL.md": SKILL_MD, "scripts/main.py": SCRIPT})
    payload = json.loads(await env.registry.get("execute").handler(
        {"target": "skill", "skill_key": env.skill.key}, call_id=call_id,
    ))
    assert payload["error"]["code"] == "SKILL_CALL_ID_INVALID"
    assert not (tmp_path / "workspace" / "outputs").exists()
