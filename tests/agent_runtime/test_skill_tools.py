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
from muad_agent_runtime.application.artifacts import ArtifactResultWriter
from muad_agent_runtime.application.executor import (
    TOOL_RESULT_ARTIFACT_BYTES,
    AgentRunnerExecutor,
    ExecutorRequest,
    ExecutorRunContext,
    ToolCallRecorder,
)
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


def _skill_md_with_body(body: str) -> str:
    """同 `SKILL_MD` 的 frontmatter，正文换成调用方给的 `body`（用于构造超大 SKILL.md）。"""
    return (
        "---\nname: demo-skill\ndescription: demo skill for runtime tests\n"
        "execution: sync\nplatform_label: Demo\n---\n\n" + body + "\n"
    )


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


def _env_for(tmp_path: Path, files: Mapping[str, str]) -> SkillEnv:
    data = _zip_bytes(files)
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


@pytest.fixture
def skill_env(tmp_path: Path) -> SkillEnv:
    """**平铺**包：`SKILL.md` 直接在 zip 根（`cd <包目录> && zip -r x.zip .` 的产物）。"""
    return _env_for(
        tmp_path,
        {
            "SKILL.md": SKILL_MD,
            "scripts/main.py": MAIN_SCRIPT,
            "scripts/other.py": OTHER_SCRIPT,
            "references/guide.md": "guide body",
            "assets/overview.txt": "asset body",
            "references/big.txt": "x" * (MAX_RESOURCE_BYTES + 256),
        },
    )


async def _call(
    registry: ToolRegistry,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    definition = registry.get(name)
    assert definition.handler is not None
    return json.loads(await definition.handler(arguments, call_id=f"call-{uuid.uuid4()}"))


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


async def test_load_skill_full_body_survives_tool_result_wrapper(tmp_path: Path) -> None:
    """[回归 2026-10-01] 超过 8KB 的 SKILL.md 正文必须**整段**到达模型。

    `load_skill` 的返回就是要给模型读的正文，属「内容投递」；被大结果外置规则换成
    `{"artifact": {...}}` 加预览，等于把工具废掉。本条同时钉住**接线**：`build_skill_registry`
    产出的真实定义必须带 `externalizable_result=False` —— 只测 executor 的判定分支是不够的，
    漏了标记照样静默外置。
    """
    body = (
        "# Demo Skill\n\n"
        + "正文放 SKILL.md，大段规范放 references。\n" * 400
        + "END-OF-INSTRUCTIONS"
    )
    env = _env_for(tmp_path, {"SKILL.md": _skill_md_with_body(body)})
    definition = env.registry.get(LOAD_SKILL_TOOL)
    assert definition.externalizable_result is False
    # 脚本输出是顺带数据，仍应保持可外置（默认 True）——标记只给内容投递类工具
    assert env.registry.get(EXECUTE_SKILL_TOOL).externalizable_result is True
    assert env.registry.get(RUN_SKILL_SCRIPT_TOOL).externalizable_result is True
    handler = definition.handler
    assert handler is not None

    raw = await handler({"skill_key": SKILL_KEY}, call_id="call-skill-1")
    assert len(raw.encode("utf-8")) > TOOL_RESULT_ARTIFACT_BYTES  # 前置条件：确实超过外置阈值

    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=f"skill-{uuid.uuid4()}",
            run_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        ),
        audit_writer=None,
        artifact_writer=ArtifactResultWriter(tmp_path / "artifacts"),
    )
    content = await recorder(
        definition, {"skill_key": SKILL_KEY}, handler=handler, call_id="call-skill-1"
    )

    assert "artifact" not in content
    payload = json.loads(content)
    assert payload["manifest"]["name"] == SKILL_KEY
    assert payload["instructions"] == body


async def test_wrapped_package_loads_from_runtime_cache(tmp_path: Path) -> None:
    """带一层包装目录的包（`zip -r x.zip <folder>`）必须能加载，且资源按**包根**解析。

    回归（2026-10-01 实测事故）：缓存把 zip **原样解包**，而运行时按「平坦根」找
    `SKILL.md` ⇒ 包装层的包在导入校验侧能过（校验侧有定位逻辑）、运行时却报
    `SKILL_PACKAGE_INVALID: missing SKILL.md`（用户侧表现：技能导入成功但 `load_skill` 加载不了）。
    夹具默认打**平铺**包，故这条路径此前无覆盖 —— 而「zip 一个文件夹」正是最自然的打包方式
    （也是 macOS Finder 压缩的形状）。定位规则现已收敛到 `muad_skill_sdk.locate_package_root`
    单点实现，两侧共用。
    """
    env = _env_for(
        tmp_path,
        {
            "demo-skill/SKILL.md": SKILL_MD,
            "demo-skill/scripts/main.py": MAIN_SCRIPT,
            "demo-skill/references/guide.md": "guide body",
        },
    )

    payload = await _call(env.registry, LOAD_SKILL_TOOL, {"skill_key": SKILL_KEY})
    assert payload["manifest"]["name"] == "demo-skill"
    assert payload["instructions"].startswith("# Demo Skill")

    guide = await _call(
        env.registry,
        READ_SKILL_RESOURCE_TOOL,
        {"skill_key": SKILL_KEY, "path": "references/guide.md"},
    )
    assert guide == {"path": "references/guide.md", "content": "guide body"}


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
