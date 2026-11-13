"""[S-11] workspace_shell：真实 bwrap、Pod 网络、系统依赖与 workspace 临时安装。

真实边界：WorkspaceShellRunner → bwrap → 真实 Python/Node 与 workspace 文件系统。
macOS 无 bwrap 时探针在带 bwrap 的 Linux 测试镜像内执行（同 E-02/S-48 口径）。
配额拒绝与 TTL 清理由 TASK-006 的 Worker 清理循环覆盖，本文件不重复实现。
"""

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
    EXECUTE_TOOL,
    SkillToolSet,
    WorkspaceShellContext,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache

from tests.acceptance.user_skill_sandbox.workspace_shell_support import run_probe

pytestmark = pytest.mark.integration


def test_s11_workspace_shell_runs_command_with_system_dependencies() -> None:
    """普通 shell 成功；默认系统依赖可用；data/output 目录真实注入。"""
    payload = run_probe("shell")

    assert payload["exit_code"] == 0
    env = payload["env"]
    assert env["tabulate"]
    assert env["data"] == "/workspace/data"
    assert env["output"] == "/workspace/outputs/shell"
    assert env["cwd"] == "/workspace"
    assert env["session_socket"] is None
    assert payload["data_file"] == "persisted"
    assert payload["output_file"]["tabulate"] == env["tabulate"]
    assert "result.json" in payload["changed_files"]


def test_s11_temp_installs_stay_inside_workspace_and_are_not_guaranteed() -> None:
    """pip/npm 临时安装只写 workspace，不写系统目录；新 Pod/空 workspace 不可恢复。"""
    payload = run_probe("install")

    assert payload["exit_code"] == 0
    assert "workspace-only" in payload["stdout"]
    assert payload["python_installed"] is True
    assert payload["node_installed"] is True
    assert payload["system_hits"] == []
    assert payload["fresh_import_exit"] != 0
    assert "ModuleNotFoundError" in payload["fresh_import_stderr"]
    changed = set(payload["changed_files"])
    assert "python/e17_demo_dep/__init__.py" in changed
    assert "node/node_modules/s11-demo-dep/package.json" in changed


def test_s11_timeout_and_output_limits_are_enforced_with_recovery() -> None:
    """超时/输出上限触发有界失败；失败后同一 workspace 可继续执行。"""
    payload = run_probe("limits")

    assert payload["timeout"]["code"] == "SANDBOX_TIMEOUT"
    assert payload["overflow"]["code"] == "SANDBOX_OUTPUT_LIMIT"
    assert payload["normal"]["code"] == "OK"
    assert payload["normal"]["exit_code"] == 0
    assert payload["normal"]["stdout"] == "ok-after-limits"


class _MemoryMetadata:
    def __init__(self) -> None:
        self.records: list[WorkspaceLeaseRecord] = []

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
        return None


async def test_s11_workspace_shell_is_registered_for_every_agent_without_skill_key(
    tmp_path,
) -> None:
    """Runtime 工具层：无 Skill 的 Agent 也注册 workspace_shell；schema 无 skill_key。"""
    metadata = _MemoryMetadata()
    layout = WorkspaceLayout(
        root=tmp_path / "workspaces",
        quota_bytes=500 * 1024 * 1024,
        user_quota_bytes=500 * 1024 * 1024,
        file_limit=5000,
        data_ttl_seconds=30 * 24 * 60 * 60,
    )
    store = WorkspaceStore(layout=layout, metadata=metadata, sandbox_mount=None)
    shell = WorkspaceShellRunner(
        store=store,
        runner_factory=lambda: DevUnsafeRunner(),
        policy=SandboxPolicy(profile=SandboxProfile.UNTRUSTED, dev_unsafe=True),
        sandbox_mount=None,
    )
    context = WorkspaceShellContext(
        tenant_id="tenant-s11",
        user_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
    )
    tool_set = SkillToolSet(
        cache=SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache"),
        skills=(),
        timeout_sec=30,
        workspace_shell=shell,
        workspace_shell_context=context,
    )
    registry = tool_set.registry()
    definition = registry.get(EXECUTE_TOOL)
    assert definition.effect is ToolEffect.EXTERNAL
    assert definition.input_schema["additionalProperties"] is False
    assert "target" in definition.input_schema["properties"]

    payload = json.loads(
        await definition.handler({"target": "workspace", "command": "printf 'tool-ok'"}, call_id="s11-call")
    )
    assert payload["exit_code"] == 0
    assert payload["stdout"] == "tool-ok"
