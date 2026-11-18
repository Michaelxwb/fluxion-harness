"""Inventory for `async-skill-background`: task document ↔ code ↔ tests ↔ specs ↔ scenarios.

The requirement has no product code of its own; this file makes the implementation surface
falsifiable. Every TASK, Spec-Ref and Scenario gets a parameterized row, and the declared test
argv is resolved against the real tree (including node ids).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from muad_agent_core.agent import AgentPolicy
from muad_agent_core.skill import SkillExecutor
from muad_agent_runtime.application.async_tools.late_results import LateBackgroundResultPump
from muad_agent_runtime.application.async_tools.materialization import materialize_late_results
from muad_agent_runtime.application.async_tools.operations import ACTIVE_OPERATION_STATUSES
from muad_agent_runtime.application.skill_tools import (
    EXECUTE_SKILL_TOOL,
    EXECUTE_TOOL,
    build_skill_registry,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    ArtifactPurpose,
    ExecutionDetail,
    OperationStatus,
    ResolvedSkill,
    SkillExecutionMode,
    TaskArtifactRef,
    TaskArtifactWriteRequest,
    TaskCenterTaskItem,
    TaskChildItem,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = ROOT / ".code-flow/tasks/2026-11-17/async-skill-background"
TASK_DOC = REQUIREMENT / "async-skill-background.md"
DESIGN = REQUIREMENT / "async-skill-background.design.md"
FRONTEND_DESIGN = REQUIREMENT / "async-skill-background.frontend.design.md"
SCENARIOS_DOC = REQUIREMENT / "async-skill-background.scenarios.md"
MOCK_HTML = REQUIREMENT / "async-skill-background.mock.html"
SPECS = ROOT / ".code-flow/specs"
TASK_CENTER = ROOT / "apps/console-platform/frontend/src/modules/webchat/task-center"
FRONTEND_TYPES = ROOT / "apps/console-platform/frontend/src/modules/webchat/types.ts"
TASK_CENTER_API = (
    ROOT / "apps/console-platform/frontend/src/modules/webchat/services/taskCenterApi.ts"
)
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"
CONFIG_INVENTORY = ROOT / "docs/configuration-inventory.csv"

EXPECTED_TASKS = tuple(f"TASK-{index:03d}" for index in range(1, 8))


def _task_sections() -> dict[str, str]:
    text = TASK_DOC.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^## (TASK-\d{3}):[^\n]*\n", text, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.end() : end]
    return sections


TASKS = _task_sections()


def _field(task_id: str, name: str) -> str | None:
    match = re.search(rf"^- \*\*{re.escape(name)}\*\*: (.*)$", TASKS[task_id], re.MULTILINE)
    return match.group(1).strip() if match else None


def _list_field(task_id: str, name: str) -> tuple[str, ...]:
    value = _field(task_id, name)
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _declared_tests(task_id: str) -> list[dict[str, Any]]:
    match = re.search(r"### Tests\s+```json\s+(.*?)\s+```", TASKS[task_id], re.DOTALL)
    assert match is not None, f"{task_id} 缺少 Tests JSON"
    parsed = json.loads(match.group(1))
    assert isinstance(parsed, list)
    return cast(list[dict[str, Any]], parsed)


SPEC_REFS = tuple(
    sorted({ref for task_id in TASKS for ref in _list_field(task_id, "Spec-Refs")})
)
SCENARIO_IDS = tuple(
    sorted({scenario for task_id in TASKS for scenario in _list_field(task_id, "Scenarios")})
)


def _glob_exists(path: str) -> bool:
    if "*" in path:
        return any(ROOT.glob(path))
    return (ROOT / path).exists()


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_task_document_declares_the_expected_seven_tasks() -> None:
    assert tuple(TASKS) == EXPECTED_TASKS


@pytest.mark.parametrize("task_id", EXPECTED_TASKS)
def test_task_metadata_is_complete(task_id: str) -> None:
    assert task_id in TASKS
    status = _field(task_id, "Status")
    if task_id == "TASK-007":
        assert status in {"draft", "in-progress", "done"}, status
    else:
        assert status == "done", f"{task_id} 尚未完成：{status}"
    assert _field(task_id, "Source"), f"{task_id} 缺少 Source"
    description = re.search(r"### Description\s+(.*?)(?=\n### )", TASKS[task_id], re.DOTALL)
    assert description is not None and description.group(1).strip(), f"{task_id} 缺少 Description"
    assert _field(task_id, "Paths"), f"{task_id} 缺少 Paths"
    assert _declared_tests(task_id), f"{task_id} 没有声明测试"
    assert _list_field(task_id, "Spec-Refs"), f"{task_id} 缺少 Spec-Refs"
    assert _field(task_id, "Scenarios") is not None, f"{task_id} 缺少 Scenarios"


@pytest.mark.parametrize("task_id", EXPECTED_TASKS)
def test_declared_test_paths_and_node_ids_exist(task_id: str) -> None:
    for entry in _declared_tests(task_id):
        argv = entry["argv"]
        assert isinstance(argv, list) and argv[:3] == ["uv", "run", "pytest"], argv
        assert "-q" in argv, "声明测试必须静默运行"
        path_args = [arg for arg in argv if isinstance(arg, str) and arg.startswith("tests/")]
        assert path_args, f"{task_id} 的测试命令没有 tests/ 路径"
        for arg in path_args:
            path, _, node = arg.partition("::")
            target = ROOT / path
            assert target.is_file(), f"{task_id} 声明的测试文件不存在：{path}"
            if node:
                assert re.search(
                    rf"^(?:async )?def {re.escape(node)}\b", target.read_text(encoding="utf-8"),
                    re.MULTILINE,
                ), f"{task_id} 声明的测试节点不存在：{arg}"
            else:
                assert re.search(
                    r"^(?:async )?def test_", target.read_text(encoding="utf-8"), re.MULTILINE
                ), f"{task_id} 声明的测试文件没有可收集用例：{path}"


@pytest.mark.parametrize("task_id", EXPECTED_TASKS)
def test_declared_paths_are_real(task_id: str) -> None:
    paths = _list_field(task_id, "Paths")
    assert paths, f"{task_id} 没有 Paths"
    for path in paths:
        assert _glob_exists(path), f"{task_id} 声明的路径不存在：{path}"


@pytest.mark.parametrize("spec_ref", SPEC_REFS)
def test_spec_refs_resolve_to_existing_rules(spec_ref: str) -> None:
    spec, _, rule = spec_ref.partition("#")
    path = SPECS / f"{spec}.md"
    assert path.is_file(), f"Spec 文件不存在：{path}"
    if rule:
        assert f"[{rule}]" in path.read_text(encoding="utf-8"), f"规则不存在：{spec_ref}"


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_every_declared_scenario_has_human_steps(scenario_id: str) -> None:
    headings = set(
        re.findall(r"^## ((?:S|E|B)-\d{2,3})\b", SCENARIOS_DOC.read_text(encoding="utf-8"), re.MULTILINE)
    )
    assert scenario_id in headings, f"场景文档缺少 {scenario_id}"


@pytest.mark.parametrize(
    "scenario_id",
    sorted(
        set(
            re.findall(
                r"^## ((?:S|E|B)-\d{2,3})\b",
                SCENARIOS_DOC.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
    ),
)
def test_no_orphan_scenario_is_documented(scenario_id: str) -> None:
    assert scenario_id in SCENARIO_IDS, f"{scenario_id} 没有归属 TASK"


def test_contracts_carry_the_background_read_and_delivery_fields() -> None:
    assert "purpose" in TaskArtifactWriteRequest.model_fields
    assert "deliveries" in TaskArtifactRef.model_fields
    assert {
        "agent_id",
        "task_type",
        "skill_name",
        "parent_id",
        "item_key",
        "artifact_total",
        "counts",
        "partial_failure",
        "children",
    } <= set(ExecutionDetail.model_fields)
    assert {"skill_name", "counts", "partial_failure", "has_artifacts"} <= set(
        TaskCenterTaskItem.model_fields
    )
    assert {"label", "item_key", "error_message_summary", "artifact_count"} <= set(
        TaskChildItem.model_fields
    )


def test_task_purpose_is_output_tool_result_or_transcript() -> None:
    assert set(ArtifactPurpose.__args__) == {"OUTPUT", "TOOL_RESULT", "TRANSCRIPT"}


def test_run_side_tool_schema_has_no_completion_mode(tmp_path: Path) -> None:
    skill = ResolvedSkill(
        skill_id=__import__("uuid").uuid4(),
        artifact_id=__import__("uuid").uuid4(),
        key="inventory-async",
        name="Inventory async",
        description="",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/inventory-async.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        source="ADMIN",
    )
    cache = SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache")
    registry = build_skill_registry(
        cache=cache,
        skills=(skill,),
        policy=AgentPolicy(deadline_ms=30_000),
        executor=cast(SkillExecutor, object()),
    )
    execute = registry.get(EXECUTE_TOOL)
    legacy = registry.get(EXECUTE_SKILL_TOOL)
    assert "completion_mode" not in execute.input_schema["properties"]
    assert "completion_mode" not in legacy.input_schema["properties"]
    assert "background" in execute.description.lower()
    assert "task center" in execute.description.lower()


def test_capacity_only_counts_active_operations() -> None:
    assert set(ACTIVE_OPERATION_STATUSES) == {
        OperationStatus.SUBMIT_PENDING,
        OperationStatus.SUBMITTED,
        OperationStatus.TASK_ACCEPTED,
        OperationStatus.RUNNING,
    }
    source = _read("apps/agent-runtime/src/muad_agent_runtime/application/async_tools/operations.py")
    assert "ToolOperation.status.in_(ACTIVE_OPERATION_STATUSES)" in source
    assert "completion_mode == CompletionMode.JOIN" in source.split("async def cancel_operations")[1]


@pytest.mark.parametrize(
    "relative,needle",
    [
        (
            "apps/agent-runtime/src/muad_agent_runtime/application/async_tools/continuation_service.py",
            "ToolOperation.completion_mode == CompletionMode.JOIN",
        ),
        (
            "apps/agent-runtime/src/muad_agent_runtime/main.py",
            "LateBackgroundResultPump",
        ),
        (
            "apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py",
            "outbox_result",
        ),
        (
            "apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py",
            "error_message=error_message",
        ),
        (
            "apps/agent-runtime/src/muad_agent_runtime/application/run_service.py",
            "ToolOperation.completion_mode == CompletionMode.DETACH",
        ),
    ],
)
def test_backend_implementation_facts_are_present(relative: str, needle: str) -> None:
    assert needle in _read(relative), f"{relative} 缺少 {needle}"


def test_late_result_pump_and_materializer_are_real() -> None:
    assert callable(materialize_late_results)
    assert LateBackgroundResultPump is not None
    task_doc = TASK_DOC.read_text(encoding="utf-8")
    delta = task_doc.split("## TASK-003:")[1].split("## TASK-004:")[0]
    assert '"ref": "RULE-001"' in delta
    assert "LateBackgroundResultPump" in delta
    assert (ROOT / "tests/agent_runtime/test_late_background_result.py").is_file()


def test_frontend_route_and_components_exist() -> None:
    page = (TASK_CENTER / "TaskCenterPage.tsx").read_text(encoding="utf-8")
    route = (TASK_CENTER / "taskCenterRoute.ts").read_text(encoding="utf-8")
    api = TASK_CENTER_API.read_text(encoding="utf-8")
    types = FRONTEND_TYPES.read_text(encoding="utf-8")
    assert "parseTaskCenterQuery" in page
    assert "BackgroundTaskListPanel" in page and "BackgroundTaskDetailPanel" in page
    assert "background" in route and "pending" in route
    assert "listTasks" in api and "/tasks" in api
    assert "children_page" in api
    assert "deliveries?:" in types, "统一 Artifact 未合入时 deliveries 必须可缺省"
    assert 'artifact_type = \'TASK_OUTPUT\'' not in page


def test_locale_titles_describe_the_combined_task_center() -> None:
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    assert zh["chat.tabs.tasks"] == "任务"
    assert zh["chat.taskCenter.label"] == "任务中心"
    assert zh["chat.taskCenter.group.background"] == "后台任务"
    assert en["chat.tabs.tasks"] == "Tasks"
    assert en["chat.taskCenter.label"] == "Task center"


def test_design_scenarios_mock_and_dependency_notes_are_consistent() -> None:
    design = DESIGN.read_text(encoding="utf-8")
    frontend = FRONTEND_DESIGN.read_text(encoding="utf-8")
    scenarios = SCENARIOS_DOC.read_text(encoding="utf-8")
    assert MOCK_HTML.is_file(), "交互稿路径失效"
    assert "async-skill-background.mock.html" in frontend
    assert "view=background&taskId=" in frontend
    assert "TaskArtifactRef.deliveries" in frontend
    assert "未合入" in frontend, "统一 Artifact 依赖未合入时必须明确标记"
    assert "产物依赖" in scenarios, "场景文档必须标明产物依赖边界"
    assert "LateBackgroundResultPump" in design
    assert "Run 侧 ASYNC = 后台任务" in design
    assert "取消不取消已提交的 DETACH" in design


def test_configuration_inventory_contains_the_touched_constants() -> None:
    with CONFIG_INVENTORY.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    symbols = {(row["path"], row["symbol"]) for row in rows}
    assert (
        "apps/agent-runtime/src/muad_agent_runtime/application/async_tools/operations.py",
        "ACTIVE_OPERATION_STATUSES",
    ) in symbols
    assert (
        "packages/contracts/src/muad_contracts/tool_runtime.py",
        "MAX_TOOL_ERROR_MESSAGE_CHARS",
    ) in symbols
    assert (
        "apps/agent-worker/src/muad_agent_worker/delivery/messages.py",
        "MAX_ERROR_CHARS",
    ) in symbols
