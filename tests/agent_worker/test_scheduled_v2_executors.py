"""TASK-016 · REMINDER / SKILL 执行器与按 `task_type` 分派（design §2.3.2 / §3.2.2）。

真实边界：真实 PostgreSQL（claim/终态事务）与真实 Skill 子进程（NFS → 本地缓存 → 执行）；
只把「另一个执行器」替成断言替身，用来证明 REMINDER 路径确实不走到 Skill/Model/Tool。

等待语义（EXTERNAL_POLL 载荷、非法返回的确定性失败）由 `test_worker_outcomes` 覆盖；
BATCH 的扇出上限与 fan-in 聚合由 `test_batch_fanout` / `test_batch_fanin` 覆盖，本文件只
证明执行器层的分派与冻结事实。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from muad_agent_worker.infrastructure.models.task import TaskDelivery, TaskExecution
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.execution_outcomes import OutcomeKind, interpret_execution
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import WaitKind
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy import select

from agent_worker.conftest import TenantContext
from agent_worker.helpers import fetch_task, persist_task

ARTIFACT_ID = "7f4a3c1e-0000-4000-8000-000000000016"
ARTIFACT_UUID = uuid.UUID(ARTIFACT_ID)
STORAGE_KEY = "skills/policy_check.zip"
TIMEOUT_SEC = default_platform_settings().task.execution_timeout_sec


class _PoisonedExecutor:
    """REMINDER 路径上它一旦被调用就说明分派错了。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("REMINDER must never reach the Skill executor")


def _build_skill_zip(root: Path, *, body: str, storage_key: str = STORAGE_KEY) -> str:
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body, encoding="utf-8")
        archive_path = root / storage_key
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((root / storage_key).read_bytes()).hexdigest()


def _cache(tmp: Path) -> SkillArtifactCache:
    store = NfsArtifactStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    return SkillArtifactCache(store, tmp / "cache")


def _skill_snapshot(checksum: str, *, argv: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "argv": list(argv),
        "skills": [
            {
                "skill_id": "skill-1",
                "artifact_id": ARTIFACT_ID,
                "key": "policy_check",
                "checksum": checksum,
                "storage_key": STORAGE_KEY,
                "execution_mode": "ASYNC",
            }
        ],
        "budget": {"execution_timeout_sec": TIMEOUT_SEC},
    }


ECHO_MAIN = (
    "import json, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "print(json.dumps({'argv': sys.argv[1:], 'input': payload}))\n"
)


# --------------------------------------------------------------- REMINDER


async def test_reminder_executes_without_model_skill_or_tool(tenant: TenantContext) -> None:
    """REMINDER 直接持久化固定文本：分派不到 Skill 执行器，结果先落库。"""
    text = "17:00 跟客户开会"
    task = await persist_task(
        tenant,
        task_type="REMINDER",
        skill_id=None,
        skill_artifact_id=None,
        input_json={"mode": "REMINDER", "reminder_text": text, "context": {}},
    )
    poisoned = _PoisonedExecutor()
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=TaskTypeExecutor(reminder=ReminderExecutor(), skill=poisoned),
        instance_id="reminder-worker",
    )

    assert await asyncio.wait_for(worker.run_once(), timeout=5) == task.id

    stored = await fetch_task(tenant, task.id)
    assert stored.status == "COMPLETED"
    assert stored.result_json == {"text": text}, "固定文本原样成为执行结果"
    assert stored.result_artifact_id is None
    assert poisoned.calls == 0, "REMINDER 不得进入 Skill/Model/Tool 路径"

    async with tenant.session_factory() as session:
        deliveries = list(
            await session.scalars(select(TaskDelivery).where(TaskDelivery.execution_id == task.id))
        )
    assert deliveries == [], "结果先落库；投递是独立边界（本任务不建投递记录）"


async def test_reminder_without_fixed_text_fails_deterministically(
    tenant: TenantContext,
) -> None:
    """缺少固定文本是确定性失败：不进重试预算，不留在无心跳的 RUNNING。"""
    task = await persist_task(
        tenant,
        task_type="REMINDER",
        skill_id=None,
        skill_artifact_id=None,
        input_json={"mode": "REMINDER"},
        max_attempts=3,
        execution_snapshot_json={"schema_version": 4, "skills": []},
    )
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=TaskTypeExecutor(reminder=ReminderExecutor(), skill=_PoisonedExecutor()),
        instance_id="reminder-worker",
    )

    await worker.run_once()

    stored = await fetch_task(tenant, task.id)
    assert (stored.status, stored.error_code) == ("FAILED", "REMINDER_TEXT_MISSING")
    assert stored.attempt == 1, "确定性失败不消耗剩余重试"
    assert stored.lease_owner is None


async def test_reminder_text_is_taken_verbatim_without_reinterpretation(
    tenant: TenantContext,
) -> None:
    """固定文本可能像指令：执行器原样返回，不解析、不补全。"""
    text = "请让模型总结今天的日报并发送到群里"
    outcome = await ReminderExecutor().execute(
        TaskExecution(task_type="REMINDER", input_json={"reminder_text": text})
    )

    assert outcome["result"] == {"text": text}
    assert interpret_execution(outcome, now=datetime.now(UTC)).kind is OutcomeKind.COMPLETED


# --------------------------------------------------------------- 分派


async def test_dispatch_routes_by_task_type_and_rejects_unwired_modes() -> None:
    """SKILL/BATCH 走 Skill 执行器；未接入的 AGENTIC 显式确定性失败，不降级成 Skill。"""
    class _Echo:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def execute(self, task: TaskExecution) -> dict[str, Any]:
            self.seen.append(task.task_type)
            return {"status": "SUCCEEDED", "result": {}}

    reminder, skill = _Echo(), _Echo()
    dispatcher = TaskTypeExecutor(reminder=reminder, skill=skill)
    for task_type in ("SKILL", "BATCH"):
        await dispatcher.execute(TaskExecution(task_type=task_type))
    assert skill.seen == ["SKILL", "BATCH"]
    assert reminder.seen == []

    with pytest.raises(TaskExecutionError) as raised:
        await dispatcher.execute(TaskExecution(task_type="AGENTIC"))
    assert raised.value.code == "TASK_TYPE_UNSUPPORTED"
    assert raised.value.retryable is False


# --------------------------------------------------------------- SKILL


async def test_skill_uses_frozen_argv_and_input_verbatim(tenant: TenantContext) -> None:
    """Skill 收到快照冻结的 argv 与本次 input 原样，不做自然语言重解释。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        checksum = _build_skill_zip(cache.store.root, body=ECHO_MAIN)
        payload = {"customers": ["A", "B"], "note": "keep  spaces"}
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_skill_snapshot(
                checksum, argv=("--company-id", "123", "--company-id", "456")
            ),
            input_json=payload,
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    assert outcome["status"] == "SUCCEEDED", outcome
    assert outcome["result"]["argv"] == ["--company-id", "123", "--company-id", "456"]
    assert outcome["result"]["input"] == payload


async def test_skill_retry_keeps_the_frozen_artifact_and_never_drifts(
    tenant: TenantContext,
) -> None:
    """同一 Task 的重试始终执行冻结版本：更新的 artifact 不会被悄悄执行。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        frozen_checksum = _build_skill_zip(cache.store.root, body=ECHO_MAIN)
        newer_body = (
            "import json, sys\n"
            "json.loads(sys.stdin.read() or '{}')\n"
            "print(json.dumps({'version': 'new'}))\n"
        )
        _build_skill_zip(cache.store.root, body=newer_body, storage_key="skills/policy_check_v2.zip")
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_skill_snapshot(frozen_checksum),
            input_json={"x": 1},
        )
        executor = SkillTaskExecutor(cache)

        first = await executor.execute(task)
        second = await executor.execute(task)

    assert first["result"] == {"argv": [], "input": {"x": 1}}
    assert second["result"] == first["result"], "重试不得切换到更新的 artifact"


async def test_retry_after_artifact_bytes_change_fails_instead_of_running_new_code(
    tenant: TenantContext,
) -> None:
    """NFS 上同一 storage_key 的内容被换掉：checksum 不匹配 ⇒ 确定性拒绝，不执行新代码。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        frozen_checksum = _build_skill_zip(cache.store.root, body=ECHO_MAIN)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_skill_snapshot(frozen_checksum),
        )
        # 新进程（空缓存）看到被替换的字节：必须按原产物协议校验 checksum。
        _build_skill_zip(cache.store.root, body="print('tampered')\n")
        fresh = SkillArtifactCache(NfsArtifactStore(tmp / "nfs"), tmp / "cache-fresh")
        with pytest.raises(TaskExecutionError) as raised:
            await SkillTaskExecutor(fresh).execute(task)

    assert raised.value.code == "SKILL_ARTIFACT_INTEGRITY_ERROR"
    assert raised.value.retryable is False


async def test_missing_artifact_reads_through_the_artifact_protocol(
    tenant: TenantContext,
) -> None:
    """缓存缺失时按原产物协议读取；NFS 没有该 key ⇒ 确定性 SKILL_ARTIFACT_UNAVAILABLE。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache = _cache(tmp)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_skill_snapshot("sha256:" + "a" * 64),
        )

        with pytest.raises(TaskExecutionError) as raised:
            await SkillTaskExecutor(cache).execute(task)

    assert raised.value.code == "SKILL_ARTIFACT_UNAVAILABLE"
    assert raised.value.retryable is False


async def test_batch_parent_parks_on_fanin_without_running_the_skill(
    tenant: TenantContext,
) -> None:
    """BATCH 父任务不重跑 Skill：返回 BATCH_FANIN 等待载荷，由 fan-in 聚合终态。"""
    batch_ref = {"batch_id": str(uuid.uuid4()), "root_id": str(uuid.uuid4())}
    task = await persist_task(
        tenant,
        task_type="BATCH",
        skill_id=uuid.uuid4(),
        skill_artifact_id=uuid.uuid4(),
        input_json={"batch": {"items": [{"customer": "A"}]}},
        wait_payload_json={"batch": batch_ref},
    )

    class _NeverCalled:
        async def ensure(self, **_kwargs: Any) -> Path:
            raise AssertionError("BATCH parent must not touch the artifact cache")

    outcome = await SkillTaskExecutor(_NeverCalled()).execute(task)

    assert outcome["result"]["wait"]["wait_kind"] == WaitKind.BATCH_FANIN.value
    assert outcome["result"]["wait"]["batch"] == batch_ref
    parsed = interpret_execution(outcome, now=datetime.now(UTC))
    assert parsed.kind is OutcomeKind.WAITING
    assert parsed.wait_kind is WaitKind.BATCH_FANIN
    assert parsed.wait_payload == {"batch": batch_ref}
