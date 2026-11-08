"""[S-10] 执行快照冻结 source/artifact/workspace/platform keys（真实 PostgreSQL）。

真实边界：`build_task_snapshot` → `task.task_execution.execution_snapshot_json` → 重新读取；
沙箱进程与 stdout 契约由 TASK-003 的 S-10 扩展覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_contracts import (
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    WorkspaceRef,
    build_task_snapshot,
    snapshot_hash,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration

TENANT = f"acc-s10-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    await session.execute(delete(TaskExecution).where(TaskExecution.tenant_id == TENANT))
    await session.commit()


def _snapshot() -> tuple[dict, ResolvedSkill, WorkspaceRef]:
    agent = ResolvedAgent(
        id=uuid.uuid4(), key="agent", revision=1, instructions="run skill", runtime_config={}
    )
    model = ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://model.example/v1",
    )
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="report-skill",
        name="Report Skill",
        description="generate report",
        version="1.0.0",
        checksum="sha256:" + "e" * 64,
        storage_key="skills/report/1.0.0/skill.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter={"platforms": ["mssp"]},
        source=SkillSource.USER,
    )
    workspace = WorkspaceRef(tenant_id=TENANT, user_id=uuid.uuid4(), agent_id=agent.id)
    snapshot = build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=workspace,
    )
    return snapshot, skill, workspace


async def test_s10_execution_snapshot_freezes_source_artifact_and_workspace(
    session: AsyncSession,
) -> None:
    """[S-10] 快照写入 task.task_execution 后重读，来源/artifact/checksum/workspace 不变。"""
    snapshot, skill, workspace = _snapshot()
    task = TaskExecution(
        tenant_id=TENANT,
        agent_id=workspace.agent_id,
        actor_user_id=workspace.user_id,
        intent_key=skill.key,
        skill_id=skill.skill_id,
        skill_artifact_id=skill.artifact_id,
        trigger_type="SCHEDULED",
        execution_mode="ASYNC",
        status="QUEUED",
        input_json={},
        execution_snapshot_schema_version=snapshot["schema_version"],
        execution_snapshot_json=snapshot,
        snapshot_hash=snapshot_hash(snapshot),
        idempotency_key=f"idem-{uuid.uuid4()}",
        delivery_key=f"task:{uuid.uuid4()}:final",
    )
    session.add(task)
    await session.commit()

    stored = await session.get(TaskExecution, task.id)
    assert stored is not None
    entry = stored.execution_snapshot_json["skills"][0]
    assert entry["skill_id"] == str(skill.skill_id)
    assert entry["artifact_id"] == str(skill.artifact_id)
    assert entry["checksum"] == skill.checksum
    assert entry["source"] == "USER"
    assert entry["platforms"] == ["mssp"]
    assert entry["workspace"] == {
        "tenant_id": TENANT,
        "user_id": str(workspace.user_id),
        "agent_id": str(workspace.agent_id),
    }
    assert stored.snapshot_hash == snapshot_hash(stored.execution_snapshot_json)


def test_s10_sandboxed_executor_runs_readonly_package_and_reports_audit() -> None:
    """[S-10] 真实 bwrap：只读 /skill + 独立 /workspace + stdout/exit code/审计事件。"""
    payload = run_probe("s10")

    result = payload["result"]
    assert payload["ok"] is True, payload
    assert result["status"] == "SUCCEEDED", result
    assert result["exit_code"] == 0
    assert result["result"]["payload"] == {"question": "hello"}
    assert result["result"]["argv"] == ["--company-id", "42", "a b; echo not-executed"]
    assert result["result"]["config"] == {"value": "relative-ok"}
    assert result["result"]["flags"] == [1, 0, 1]
    assert payload["helper_imported"] is True
    assert len(result["outputs"]) == 1
    output = result["outputs"][0]
    assert output["relative_path"] == "result.json"
    assert output["size"] > 0
    assert output["checksum"].startswith("sha256:")
    assert payload["output_file_exists"] is True
    # 执行器不写 READY/缓存，包的树 checksum 前后一致。
    before, after = payload["package_checksum_before_after"]
    assert before == after
    assert payload["events"] == [
        {"source": "USER", "status": "SUCCEEDED", "output_count": 1}
    ]
