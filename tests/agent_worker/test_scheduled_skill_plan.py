"""TASK-016 · SKILL_PLAN 确定性运行时（design §10.3 / §10.6）。

一条事实一个用例：Fire 时按 Schedule 的 IANA 时区渲染 `input`/`argv`/`window` 并冻结进
Snapshot（含 hash），执行器只把业务 `input` 交给 stdin、把 `argv` 追加到脚本，重试复用同一
份冻结事实，旧任务没有 `argv` 时按空数组执行。

真实边界：真实 PostgreSQL（Fire/Execution 写入）与真实 Skill 子进程；只把 Console 的依赖
取数替成协议实现。失败路径直接复用 TASK-008 的 `ExecutionOutcome`，不解析文本。
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskScheduleRevision
from muad_agent_worker.worker.execution_outcomes import OutcomeKind, interpret_execution
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import ScheduleMode, SkillExecutionMode
from muad_contracts.snapshot import (
    load_schedule_snapshot,
    schedule_snapshot_bytes,
    schedule_snapshot_hash,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_task, persist_task
from agent_worker.test_scheduled_v2_fire import (
    MaterialsClient,
    executions,
    loop_for,
    seed,
)
from agent_worker.test_scheduled_v2_snapshot import make_materials

pytestmark = pytest.mark.integration

SHANGHAI = "Asia/Shanghai"
NEW_YORK = "America/New_York"
#: 上海 09:00 / 纽约 09:00（DST 已生效）/ 纽约 09:00（回到标准时间）。
SLOT = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
NY_DST_SLOT = datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
NY_STANDARD_SLOT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
SKILL = uuid.UUID("5a1d0c1e-0000-4000-8000-000000016016")
STORAGE_KEY = "skills/scheduled_skill_plan.zip"

ECHO_MAIN = (
    "import json, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "print(json.dumps({'argv': sys.argv[1:], 'input': payload}))\n"
)
ARGV_CONTRACT = {
    "version": 1,
    "invocation": "argv",
    "schema": {
        "type": "object",
        "properties": {
            "company-id": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "now": {"type": "string", "format": "date-time"},
            "extra": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    },
}
FAIL_MAIN = (
    "import json, sys\n"
    "print('report generated successfully')\n"
    "print(json.dumps({'ok': False, 'error': {'code': 'PLATFORM_AUTH_REJECTED', "
    "'message': 'mssw 401', 'retryable': False}}), file=sys.stderr)\n"
    "sys.exit(3)\n"
)


def _cache(tmp: Path) -> SkillArtifactCache:
    store = NfsArtifactStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    return SkillArtifactCache(store, tmp / "cache")


def _build_skill(cache: SkillArtifactCache, body: str) -> str:
    with tempfile.TemporaryDirectory() as staging:
        script = Path(staging) / "scripts"
        script.mkdir()
        (script / "main.py").write_text(body, encoding="utf-8")
        archive_path = cache.store.root / STORAGE_KEY
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(script / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((cache.store.root / STORAGE_KEY).read_bytes()).hexdigest()


def _materials(
    checksum: str,
    mode: SkillExecutionMode = SkillExecutionMode.ASYNC,
    *,
    invocation: str = "stdin",
):
    materials = make_materials(mode=ScheduleMode.SKILL, skill_id=SKILL)
    [skill] = materials.skills
    frontmatter = skill.frontmatter
    if invocation == "argv":
        frontmatter = {**frontmatter, "invocation_contract": ARGV_CONTRACT}
    return materials.model_copy(
        update={
            "skills": [
                skill.model_copy(
                    update={
                        "checksum": checksum,
                        "storage_key": STORAGE_KEY,
                        "execution_mode": mode,
                        "frontmatter": frontmatter,
                    }
                )
            ]
        }
    )


def _payload(
    *,
    input_template: dict[str, Any],
    argv: list[str],
) -> dict[str, Any]:
    return {
        "kind": "SKILL_PLAN",
        "skill_id": str(SKILL),
        "input": input_template,
        "argv": argv,
        "window": {"kind": "DAY", "offset": -1},
    }


def _echo_payload() -> dict[str, Any]:
    return _payload(
        input_template={"company_id": "22921947", "report_day": "{{previous_day}}"},
        argv=[],
    )


def _argv_payload() -> dict[str, Any]:
    return _payload(
        input_template={},
        argv=["--company-id", "22921947", "--date", "{{previous_day}}"],
    )


async def _fire(
    tenant: TenantContext,
    payload: dict[str, Any],
    *,
    checksum: str,
    timezone: str = SHANGHAI,
    slot: datetime = SLOT,
    mode: SkillExecutionMode = SkillExecutionMode.ASYNC,
    invocation: str = "stdin",
):
    seeded = await seed(tenant, next_fire_at=slot, timezone=timezone, payload=payload)
    loop = loop_for(tenant, MaterialsClient(_materials(checksum, mode, invocation=invocation)))
    execution_id = await loop.run_once(now=slot + timedelta(seconds=1))
    assert execution_id is not None
    [execution] = [
        item for item in await executions(tenant, seeded.schedule_id) if item.id == execution_id
    ]
    return seeded, execution


async def _refetch(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    return await fetch_task(tenant, execution_id)


async def _retry_row(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .values(attempt=2)
        )
    return await fetch_task(tenant, execution_id)


# ------------------------------------------------------------------ Fire 渲染与冻结


@pytest.mark.parametrize(
    ("timezone", "slot", "fire_date", "previous_day", "fire_time"),
    [
        (SHANGHAI, SLOT, "2026-03-08", "2026-03-07", "2026-03-08T09:00:00+08:00"),
        # 2026-03-08 纽约进入 EDT：同样的 09:00 槽必须用 -04:00，而不是服务器本地时区。
        (NEW_YORK, NY_DST_SLOT, "2026-03-08", "2026-03-07", "2026-03-08T09:00:00-04:00"),
        # 2026-11-01 纽约回到 EST：同一时刻的偏移变为 -05:00。
        (NEW_YORK, NY_STANDARD_SLOT, "2026-11-01", "2026-10-31", "2026-11-01T09:00:00-05:00"),
    ],
)
async def test_fire_renders_skill_plan_in_schedule_timezone(
    tenant: TenantContext,
    timezone: str,
    slot: datetime,
    fire_date: str,
    previous_day: str,
    fire_time: str,
) -> None:
    """`{{fire_date}}`/`{{previous_day}}`/`{{fire_time}}` 按 Schedule 时区渲染进 argv 并冻结。"""
    payload = _payload(
        input_template={},
        argv=["--date", "{{previous_day}}", "--now", "{{fire_time}}", "--extra", "{{fire_date}}"],
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = _cache(Path(tmpdir))
        checksum = _build_skill(cache, ECHO_MAIN)
        _seeded, execution = await _fire(
            tenant,
            payload,
            checksum=checksum,
            timezone=timezone,
            slot=slot,
            invocation="argv",
        )

    expected_input: dict[str, Any] = {}
    expected_argv = ["--date", previous_day, "--now", fire_time, "--extra", fire_date]
    assert execution.input_json["input"] == expected_input
    assert execution.input_json["argv"] == expected_argv

    snapshot = execution.execution_snapshot_json
    assert snapshot["input"] == expected_input
    assert snapshot["argv"] == expected_argv
    assert snapshot["window"] == {"kind": "DAY", "offset": -1}
    assert execution.snapshot_hash == schedule_snapshot_hash(snapshot)
    assert execution.snapshot_bytes == schedule_snapshot_bytes(snapshot)
    rebuilt = load_schedule_snapshot(snapshot)
    assert rebuilt.input == expected_input
    assert rebuilt.argv == expected_argv
    assert rebuilt.window == {"kind": "DAY", "offset": -1}


async def test_snapshot_hash_covers_rendered_input_argv_and_window(
    tenant: TenantContext,
) -> None:
    """参数进入 Snapshot 文本：任何一项变化都会改变 hash。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = _cache(Path(tmpdir))
        checksum = _build_skill(cache, ECHO_MAIN)
        _seeded, execution = await _fire(tenant, _echo_payload(), checksum=checksum)

    snapshot = execution.execution_snapshot_json
    changed_argv = {
        **snapshot,
        "argv": [*snapshot["argv"], "--extra", "1"],
    }
    changed_input = {**snapshot, "input": {**snapshot["input"], "company_id": "other"}}
    changed_window = {**snapshot, "window": {"kind": "DAY", "offset": 0}}
    for changed in (changed_argv, changed_input, changed_window):
        assert schedule_snapshot_hash(changed) != execution.snapshot_hash


async def test_old_revision_without_argv_freezes_empty_argv(
    tenant: TenantContext,
) -> None:
    """旧 Revision 没有 argv/window 时按契约默认值冻结，执行器得到空参数。"""
    payload = {
        "kind": "SKILL_PLAN",
        "skill_id": str(SKILL),
        "input": {"report_day": "{{previous_day}}"},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = _cache(Path(tmpdir))
        checksum = _build_skill(cache, ECHO_MAIN)
        _seeded, execution = await _fire(tenant, payload, checksum=checksum)

    assert execution.execution_snapshot_json["argv"] == []
    assert execution.execution_snapshot_json["window"] == {"kind": "DAY", "offset": 0}
    assert execution.input_json["argv"] == []
    assert execution.input_json["input"] == {"report_day": "2026-03-07"}


# ------------------------------------------------------------------ 执行器 argv/stdin


@pytest.mark.parametrize("mode", [SkillExecutionMode.SYNC, SkillExecutionMode.ASYNC])
async def test_legacy_execution_forwards_frozen_stdin_and_argv(
    tenant: TenantContext, mode: SkillExecutionMode
) -> None:
    """契约前冻结的执行两种通道都原样转发：stdin 只有业务 input，argv 追加到脚本。

    新调度由 invocation contract 保证单通道；这里覆盖旧 Execution 的向后执行口径。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        checksum = _build_skill(cache, ECHO_MAIN)
        artifact_id = uuid.uuid4()
        task = await persist_task(
            tenant,
            trigger_type="SCHEDULED",
            skill_artifact_id=artifact_id,
            execution_snapshot_json={
                "schema_version": 4,
                "skills": [
                    {
                        "artifact_id": str(artifact_id),
                        "checksum": checksum,
                        "storage_key": STORAGE_KEY,
                        "source": "ADMIN",
                        "execution_mode": mode.value,
                    }
                ],
                "argv": ["--company-id", "22921947", "--date", "2026-03-07"],
            },
            input_json={
                "mode": "SKILL",
                "skill_id": str(SKILL),
                "input": {"company_id": "22921947", "report_day": "2026-03-07"},
                "argv": ["--company-id", "22921947", "--date", "2026-03-07"],
                "context": {},
            },
        )

        result = await SkillTaskExecutor(cache).execute(task)

    assert result["status"] == "SUCCEEDED", result
    assert result["result"] == {
        "argv": ["--company-id", "22921947", "--date", "2026-03-07"],
        "input": {"company_id": "22921947", "report_day": "2026-03-07"},
    }


async def test_retry_reuses_frozen_argv_input_and_window(
    tenant: TenantContext,
) -> None:
    """Revision 后续改动不影响重试：同一 Task 只读 Fire 冻结的快照与 input。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        checksum = _build_skill(cache, ECHO_MAIN)
        seeded, execution = await _fire(
            tenant, _argv_payload(), checksum=checksum, invocation="argv"
        )

        async with tenant.session_factory() as session, session.begin():
            revision = await session.get(TaskScheduleRevision, seeded.revision_id)
            assert revision is not None
            revision.payload_json = {
                **_argv_payload(),
                "input": {},
                "argv": ["--company-id", "99999999", "--date", "2099-01-01"],
            }
            revision.timezone = "UTC"

        executor = SkillTaskExecutor(cache)
        first = await executor.execute(await _refetch(tenant, execution.id))
        retried = await _retry_row(tenant, execution.id)
        second = await executor.execute(retried)

    expected = {
        "argv": ["--company-id", "22921947", "--date", "2026-03-07"],
        "input": {},
    }
    assert first["result"] == expected
    assert second["result"] == expected, "重试不得按 Revision 或当前时间重新渲染"
    assert retried.execution_snapshot_json["argv"] == expected["argv"]
    assert retried.execution_snapshot_json["window"] == {"kind": "DAY", "offset": -1}


async def test_old_scheduled_task_without_argv_uses_empty_argv_and_keeps_stdin(
    tenant: TenantContext,
) -> None:
    """旧 Execution 没有 argv 时按空数组执行；旧信封的 input 仍原样进入 stdin。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        checksum = _build_skill(cache, ECHO_MAIN)
        artifact_id = uuid.uuid4()
        task = await persist_task(
            tenant,
            trigger_type="SCHEDULED",
            skill_artifact_id=artifact_id,
            execution_snapshot_json={
                "schema_version": 4,
                "skills": [
                    {
                        "artifact_id": str(artifact_id),
                        "checksum": checksum,
                        "storage_key": STORAGE_KEY,
                        "source": "ADMIN",
                        "execution_mode": "ASYNC",
                    }
                ],
            },
            input_json={
                "mode": "SKILL",
                "skill_id": str(SKILL),
                "input": {"report_day": "2026-03-07"},
                "context": {},
            },
        )

        result = await SkillTaskExecutor(cache).execute(task)

    assert result["status"] == "SUCCEEDED", result
    assert result["result"] == {"argv": [], "input": {"report_day": "2026-03-07"}}


async def test_skill_failure_reuses_the_execution_outcome_contract(
    tenant: TenantContext,
) -> None:
    """非零退出 + 结构化失败信封直接产出 ExecutionOutcome；stdout 文案不算事实。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        checksum = _build_skill(cache, FAIL_MAIN)
        _seeded, execution = await _fire(tenant, _echo_payload(), checksum=checksum)

        execution_payload = await SkillTaskExecutor(cache).execute(execution)

    outcome = interpret_execution(execution_payload, now=datetime.now(UTC))
    assert outcome.kind is OutcomeKind.RETRYABLE_FAILURE
    assert outcome.business_status == "FAILED"
    assert outcome.failure_layer == "CONTENT"
    assert outcome.error_code == "PLATFORM_AUTH_REJECTED"
    assert outcome.error_message == "mssw 401"
    assert outcome.exit_code == 3
    assert outcome.retryable is False
    assert "report generated successfully" not in outcome.evidence_preview
