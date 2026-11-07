"""workspace_shell Runtime 工具：全 Agent 注册、无 skill_key、真实子进程执行。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_core.sandbox import (
    DevUnsafeRunner,
    SandboxPolicy,
    SandboxProfile,
    WorkspaceLayout,
    WorkspaceLeaseRecord,
    WorkspaceShellRunner,
    WorkspaceStore,
)
from muad_agent_core.tools import ToolEffect
from muad_agent_runtime.application.skill_tools import (
    WORKSPACE_SHELL_TOOL,
    SkillToolSet,
    WorkspaceShellContext,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache


class _MemoryMetadata:
    """仅用于工具层测试的租约记录；数据库/目录契约由 test_workspace_store.py 覆盖。"""

    def __init__(self) -> None:
        self.records: list[WorkspaceLeaseRecord] = []
        self.released: list[uuid.UUID] = []

    async def acquire(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        execution_id: str,
        run_id: str,
        task_id: str,
        lease_sec: float,
    ) -> WorkspaceLeaseRecord:
        record = WorkspaceLeaseRecord(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            execution_id=execution_id,
            lease_until=datetime.now(UTC) + timedelta(seconds=lease_sec),
        )
        self.records.append(record)
        return record

    async def release(self, lease_id: uuid.UUID) -> None:
        self.released.append(lease_id)


def _context() -> WorkspaceShellContext:
    return WorkspaceShellContext(
        tenant_id="tenant-shell",
        user_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
    )


def _tool_set(
    tmp_path, metadata: _MemoryMetadata, context: WorkspaceShellContext | None = None
) -> tuple[SkillToolSet, WorkspaceShellContext]:
    active = context or _context()
    layout = WorkspaceLayout(
        root=tmp_path / "workspaces",
        quota_bytes=500 * 1024 * 1024,
        user_quota_bytes=500 * 1024 * 1024,
        file_limit=5000,
        data_ttl_seconds=30 * 24 * 60 * 60,
    )
    store = WorkspaceStore(layout=layout, metadata=metadata, sandbox_mount=None)
    runner = WorkspaceShellRunner(
        store=store,
        runner_factory=lambda: DevUnsafeRunner(),
        policy=SandboxPolicy(profile=SandboxProfile.UNTRUSTED, dev_unsafe=True),
        sandbox_mount=None,
    )
    return (
        SkillToolSet(
            cache=SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache"),
            skills=(),
            timeout_sec=30,
            workspace_shell=runner,
            workspace_shell_context=active,
        ),
        active,
    )


def test_workspace_shell_is_registered_for_agent_without_skills(tmp_path) -> None:
    """没有 Skill 的 Agent 也拿到 workspace_shell；schema 不接受 skill_key。"""
    tool_set, _ = _tool_set(tmp_path, _MemoryMetadata())
    registry = tool_set.registry()

    assert [definition.name for definition in registry.list()] == [WORKSPACE_SHELL_TOOL]
    definition = registry.get(WORKSPACE_SHELL_TOOL)
    assert definition.effect is ToolEffect.EXTERNAL
    assert definition.input_schema["additionalProperties"] is False
    assert definition.input_schema["required"] == ["command"]
    properties = definition.input_schema["properties"]
    assert "skill_key" not in properties
    assert set(properties) == {"command", "cwd", "timeout_sec"}


async def test_workspace_shell_tool_runs_real_process_and_reports_output_files(tmp_path) -> None:
    """工具 → WorkspaceShellRunner → 真实子进程；租约释放且 output 文件被登记。"""
    metadata = _MemoryMetadata()
    tool_set, context = _tool_set(tmp_path, metadata)
    definition = tool_set.registry().get(WORKSPACE_SHELL_TOOL)

    payload = json.loads(
        await definition.handler({"command": "printf 'hello'"}, call_id="call-1")  # type: ignore[misc]
    )
    assert payload["exit_code"] == 0
    assert payload["stdout"] == "hello"
    assert payload["changed_files"] == []
    assert metadata.released

    written = json.loads(
        await definition.handler(
            {"command": "printf 'artifact' > \"$SKILL_OUTPUT_DIR/result.txt\""},
            call_id="call-2",  # type: ignore[misc]
        )
    )
    assert written["exit_code"] == 0
    assert written["changed_files"] == ["result.txt"]
    record = metadata.records[-1]
    output = (
        tmp_path
        / "workspaces"
        / context.tenant_id
        / context.user_id
        / context.agent_id
        / "outputs"
        / record.execution_id
    )
    assert (output / "result.txt").read_text(encoding="utf-8") == "artifact"


async def test_workspace_shell_tool_maps_invalid_cwd_to_validation_error(tmp_path) -> None:
    """cwd 越出 /workspace 时返回明确错误，不执行命令。"""
    metadata = _MemoryMetadata()
    tool_set, _ = _tool_set(tmp_path, metadata)
    definition = tool_set.registry().get(WORKSPACE_SHELL_TOOL)

    payload = json.loads(
        await definition.handler(  # type: ignore[misc]
            {"command": "pwd", "cwd": "/etc"}, call_id="call-cwd"
        )
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
    assert metadata.records == []


@pytest.mark.parametrize("command", ["", "   "])
async def test_workspace_shell_tool_rejects_empty_command(tmp_path, command: str) -> None:
    """空命令在工具边界拒绝。"""
    tool_set, _ = _tool_set(tmp_path, _MemoryMetadata())
    definition = tool_set.registry().get(WORKSPACE_SHELL_TOOL)

    payload = json.loads(
        await definition.handler({"command": command}, call_id="call-empty")  # type: ignore[misc]
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
