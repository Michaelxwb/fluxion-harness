import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.agent import AgentPolicy, AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
)
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.executor import AgentRunnerExecutor, ExecutorRequest
from muad_agent_runtime.application.skill_tools import (
    EXECUTE_SKILL_TOOL,
    LOAD_SKILL_TOOL,
    MAX_RESOURCE_BYTES,
    READ_SKILL_RESOURCE_TOOL,
    RUN_SKILL_SCRIPT_TOOL,
    build_skill_registry,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill

SKILL_KEY = "demo-skill"
STORAGE_KEY = "skills/demo-skill/1.0.0/skill.zip"
SKILL_MD = """---
name: demo-skill
description: demo skill for runtime tests
execution: sync
platform_label: Demo
---

# Demo Skill

Run scripts/main.py when the user asks.
"""
MAIN_SCRIPT = (
    "import json, sys\n"
    "payload = json.load(sys.stdin)\n"
    "print(json.dumps({'echo': payload, 'script': 'main'}))\n"
)
OTHER_SCRIPT = "import json\nprint(json.dumps({'script': 'other'}))\n"


@dataclass(frozen=True)
class SkillEnv:
    registry: ToolRegistry
    skill: ResolvedSkill
    cache: SkillArtifactCache


def _zip_bytes(files: Mapping[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def skill_env(tmp_path: Path) -> SkillEnv:
    data = _zip_bytes(
        {
            "SKILL.md": SKILL_MD,
            "scripts/main.py": MAIN_SCRIPT,
            "scripts/other.py": OTHER_SCRIPT,
            "references/guide.md": "guide body",
            "assets/overview.txt": "asset body",
            "references/big.txt": "x" * (MAX_RESOURCE_BYTES + 256),
        }
    )
    artifact_root = tmp_path / "artifacts"
    artifact_path = artifact_root / STORAGE_KEY
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(data)
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key=SKILL_KEY,
        name="Demo Skill",
        description="demo skill for runtime tests",
        version="1.0.0",
        checksum="sha256:" + hashlib.sha256(data).hexdigest(),
        storage_key=STORAGE_KEY,
        execution_mode="SYNC",
    )
    cache = SkillArtifactCache(NfsArtifactStore(artifact_root), tmp_path / "cache")
    registry = build_skill_registry(
        cache=cache,
        skills=(skill,),
        policy=AgentPolicy(deadline_ms=30_000),
    )
    return SkillEnv(registry=registry, skill=skill, cache=cache)


async def _call(
    registry: ToolRegistry,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    definition = registry.get(name)
    assert definition.handler is not None
    return json.loads(await definition.handler(arguments))


def _error_code(payload: Mapping[str, Any]) -> str:
    error = payload["error"]
    assert isinstance(error, Mapping)
    code = error["code"]
    assert isinstance(code, str)
    return code


async def test_load_skill_returns_manifest_and_instructions(skill_env: SkillEnv) -> None:
    payload = await _call(skill_env.registry, LOAD_SKILL_TOOL, {"skill_key": SKILL_KEY})

    assert payload["manifest"] == {
        "name": SKILL_KEY,
        "description": "demo skill for runtime tests",
        "execution": "SYNC",
        "platform_label": "Demo",
    }
    assert payload["instructions"].startswith("# Demo Skill")
    assert "Run scripts/main.py" in payload["instructions"]
    assert "name: demo-skill" not in payload["instructions"]


async def test_read_skill_resource_reads_references_and_assets(skill_env: SkillEnv) -> None:
    guide = await _call(
        skill_env.registry,
        READ_SKILL_RESOURCE_TOOL,
        {"skill_key": SKILL_KEY, "path": "references/guide.md"},
    )
    asset = await _call(
        skill_env.registry,
        READ_SKILL_RESOURCE_TOOL,
        {"skill_key": SKILL_KEY, "path": "assets/overview.txt"},
    )

    assert guide == {"path": "references/guide.md", "content": "guide body"}
    assert asset == {"path": "assets/overview.txt", "content": "asset body"}


async def test_read_skill_resource_rejects_traversal(skill_env: SkillEnv) -> None:
    for path in ("../escape.txt", "references/../../escape.txt", "/etc/passwd"):
        payload = await _call(
            skill_env.registry,
            READ_SKILL_RESOURCE_TOOL,
            {"skill_key": SKILL_KEY, "path": path},
        )
        assert _error_code(payload) == "COMMON_VALIDATION_ERROR"


async def test_read_skill_resource_caps_size(skill_env: SkillEnv) -> None:
    payload = await _call(
        skill_env.registry,
        READ_SKILL_RESOURCE_TOOL,
        {"skill_key": SKILL_KEY, "path": "references/big.txt"},
    )

    content = payload["content"]
    assert isinstance(content, str)
    assert len(content) == MAX_RESOURCE_BYTES


async def test_execute_skill_runs_entry_script(skill_env: SkillEnv) -> None:
    payload = await _call(
        skill_env.registry,
        EXECUTE_SKILL_TOOL,
        {"skill_key": SKILL_KEY, "input": {"question": "hi"}},
    )

    assert payload["status"] == "SUCCEEDED"
    assert payload["exit_code"] == 0
    assert payload["result"] == {"echo": {"question": "hi"}, "script": "main"}


async def test_run_skill_script_runs_named_script(skill_env: SkillEnv) -> None:
    for script in ("scripts/other.py", "other.py"):
        payload = await _call(
            skill_env.registry,
            RUN_SKILL_SCRIPT_TOOL,
            {"skill_key": SKILL_KEY, "script": script, "input": {"question": "hi"}},
        )
        assert payload["status"] == "SUCCEEDED"
        assert payload["result"] == {"script": "other"}


async def test_run_skill_script_rejects_invalid_name(skill_env: SkillEnv) -> None:
    traversal = await _call(
        skill_env.registry,
        RUN_SKILL_SCRIPT_TOOL,
        {"skill_key": SKILL_KEY, "script": "../main.py"},
    )
    missing = await _call(
        skill_env.registry,
        RUN_SKILL_SCRIPT_TOOL,
        {"skill_key": SKILL_KEY, "script": "missing.py"},
    )

    assert _error_code(traversal) == "COMMON_VALIDATION_ERROR"
    assert _error_code(missing) == "SKILL_SCRIPT_NOT_FOUND"


@pytest.mark.parametrize(
    "tool_name",
    (LOAD_SKILL_TOOL, READ_SKILL_RESOURCE_TOOL, EXECUTE_SKILL_TOOL, RUN_SKILL_SCRIPT_TOOL),
)
async def test_unknown_skill_is_a_tool_error(skill_env: SkillEnv, tool_name: str) -> None:
    payload = await _call(
        skill_env.registry,
        tool_name,
        {"skill_key": "not-effective", "path": "references/guide.md", "script": "main.py"},
    )

    assert _error_code(payload) == "SKILL_NOT_EFFECTIVE"


async def test_missing_artifact_surfaces_unavailable_code(skill_env: SkillEnv) -> None:
    missing = skill_env.skill.model_copy(
        update={"storage_key": "skills/demo-skill/missing.zip"}
    )
    registry = build_skill_registry(
        cache=skill_env.cache,
        skills=(missing,),
        policy=AgentPolicy(deadline_ms=30_000),
    )

    payload = await _call(registry, LOAD_SKILL_TOOL, {"skill_key": SKILL_KEY})

    assert _error_code(payload) == "SKILL_ARTIFACT_UNAVAILABLE"


class ScriptedProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall(
                        id="call-1",
                        name=EXECUTE_SKILL_TOOL,
                        arguments={"skill_key": SKILL_KEY, "input": {"question": "hi"}},
                    ),
                ),
            )
        return ModelResponse(content="final answer", finish_reason="stop")


async def _never_cancelled() -> bool:
    return False


def _executor_request(skill: ResolvedSkill) -> ExecutorRequest:
    return ExecutorRequest(
        agent=ResolvedAgent(
            id=uuid.uuid4(),
            key="demo-agent",
            revision=1,
            instructions="be helpful",
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="https://llm.test/v1",
        ),
        input_text="use the demo skill",
        is_cancel_requested=_never_cancelled,
        skills=(skill,),
    )


async def test_full_run_executes_skill_tool_and_feeds_result_to_model(skill_env: SkillEnv) -> None:
    provider = ScriptedProvider()
    runner = AgentRunner(
        provider=provider,
        registry=skill_env.registry,
        hooks=HookPipeline(),
    )
    executor = AgentRunnerExecutor(runner=runner, request=_executor_request(skill_env.skill))

    events = [event async for event in executor.run()]

    final_text = "".join(str(event.data.get("delta", "")) for event in events)
    assert final_text == "final answer"
    assert len(provider.requests) == 2
    assert [tool.name for tool in provider.requests[0].tools] == [
        LOAD_SKILL_TOOL,
        READ_SKILL_RESOURCE_TOOL,
        EXECUTE_SKILL_TOOL,
        RUN_SKILL_SCRIPT_TOOL,
    ]
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.role is ModelRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    payload = json.loads(tool_message.content)
    assert payload["status"] == "SUCCEEDED"
    assert payload["result"] == {"echo": {"question": "hi"}, "script": "main"}
