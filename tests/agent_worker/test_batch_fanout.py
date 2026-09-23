"""B-118：BATCH Parent 幂等 fan-out 与并发上限（设计 §3.2.4）。

真实边界：真实 Skill 执行（NFS → emptyDir READY cache → 子进程）返回 TaskPlan，
由 BatchFanoutService 在真实 PostgreSQL 事务内创建 Parent/Child。不 mock 存储、
不 mock partial unique，也不 mock Worker 的 WAITING 落库。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from conftest import TenantContext
from helpers import fetch_task, persist_task
from muad_agent_worker.application.batch_fanout import (
    AGGREGATE_ALL,
    PARKED_NOT_BEFORE,
    BatchFanoutService,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings
from sqlalchemy import select

SKILL_MAIN = (
    "import json, sys\n"
    "json.loads(sys.stdin.read() or '{}')\n"
    "print(json.dumps({'batch': {'items': ["
    "{'customer': 'A'}, {'customer': 'B'}, {'customer': 'C'}, {'customer': 'D'}"
    "], 'max_concurrency': 2, 'aggregate_mode': 'ALL'}}))\n"
)

STORAGE_KEY = "skills/batch_check.zip"
ARTIFACT_ID = "7f4a3c1e-0000-4000-8000-000000000002"
ARTIFACT_UUID = uuid.UUID(ARTIFACT_ID)


def _build_skill_zip(root: Path, *, body: str = SKILL_MAIN) -> str:
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body, encoding="utf-8")
        archive_path = root / STORAGE_KEY
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((root / STORAGE_KEY).read_bytes()).hexdigest()


def _snapshot(checksum: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": {"key": "agent"},
        "model": {"key": "model"},
        "skills": [
            {
                "skill_id": "skill-1",
                "artifact_id": ARTIFACT_ID,
                "key": "policy_check",
                "version": "1",
                "checksum": checksum,
                "storage_key": STORAGE_KEY,
                "execution_mode": "ASYNC",
            }
        ],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"max_tokens": 1024},
    }


def _batch_executor(tenant: TenantContext, cache: SkillArtifactCache) -> SkillTaskExecutor:
    return SkillTaskExecutor(
        cache, fanout=BatchFanoutService(tenant.session_factory, tenant.settings)
    )


async def _children(tenant: TenantContext, parent_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(TaskExecution)
                    .where(TaskExecution.parent_id == parent_id)
                    .order_by(TaskExecution.create_time.asc(), TaskExecution.item_key.asc())
                )
            )
            .scalars()
            .all()
        )


async def _parent_with_batch_plan(tenant: TenantContext) -> tuple[Path, tempfile.TemporaryDirectory[str], SkillArtifactCache, TaskExecution]:
    tmpdir = tempfile.TemporaryDirectory()
    tmp = Path(tmpdir.name)
    store = NfsArtifactStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    cache = SkillArtifactCache(store, tmp / "cache")
    checksum = _build_skill_zip(store.root)
    parent = await persist_task(
        tenant,
        skill_artifact_id=ARTIFACT_UUID,
        execution_snapshot_json=_snapshot(checksum),
        input_json={"customers": ["A", "B", "C", "D"]},
    )
    return tmp, tmpdir, cache, parent


async def test_b118_executor_fans_out_idempotent_children_inheriting_root_and_intent(
    tenant: TenantContext,
) -> None:
    """Skill 返回 BATCH plan：同事务幂等创建 Child，root/intent/snapshot 继承，重放不增。"""
    _, tmpdir, cache, parent = await _parent_with_batch_plan(tenant)
    executor = _batch_executor(tenant, cache)

    envelope = await executor.execute(parent)
    assert envelope["result"]["wait"]["external_ref"]["batch"]["aggregate_mode"] == AGGREGATE_ALL

    children = await _children(tenant, parent.id)
    assert len(children) == 4
    for child in children:
        assert child.parent_id == parent.id
        assert child.root_id == parent.id, "Child 必须继承 root_id"
        assert child.intent_key == parent.intent_key
        assert child.agent_id == parent.agent_id
        assert child.actor_user_id == parent.actor_user_id
        assert child.skill_id == parent.skill_id
        assert child.skill_artifact_id == parent.skill_artifact_id
        assert child.snapshot_hash == parent.snapshot_hash, "Child 复用 Parent 冻结快照"
        assert child.task_type == "SKILL"
        assert child.status == "QUEUED"
        assert child.idempotency_key == f"parent:{parent.id}:{child.item_key}"
        assert child.delivery_mode == "NONE"
        assert child.delivery_status == "NONE"

    refreshed_parent = await fetch_task(tenant, parent.id)
    assert refreshed_parent.task_type == "BATCH"
    assert refreshed_parent.external_ref_json["batch"]["item_count"] == 4

    replay = await executor.execute(parent)
    assert replay["result"]["wait"]["external_ref"]["batch"]["aggregate_mode"] == AGGREGATE_ALL
    assert len(await _children(tenant, parent.id)) == 4, "重放不得新增 Child"

    concurrent = await asyncio.gather(executor.execute(parent), executor.execute(parent))
    assert all("wait" in item["result"] for item in concurrent)
    assert len(await _children(tenant, parent.id)) == 4, "并发 fan-out 不得新增 Child"
    tmpdir.cleanup()


async def test_b118_concurrency_clamps_to_min_of_plan_system_and_platform(
    tenant: TenantContext,
) -> None:
    """并发上限 = min(plan.max_concurrency, system_max, platform_limit)，超出项先停放。"""
    _, tmpdir, cache, parent = await _parent_with_batch_plan(tenant)
    executor = _batch_executor(tenant, cache)
    await executor.execute(parent)

    now = datetime.now(UTC)
    children = await _children(tenant, parent.id)
    claimable = [child for child in children if child.not_before <= now]
    parked = [child for child in children if child.not_before == PARKED_NOT_BEFORE]
    assert len(claimable) == 2, "plan.max_concurrency=2 只释放两个 Child"
    assert len(parked) == 2, "其余 Child 停放等待 fan-in 释放"

    service = BatchFanoutService(tenant.session_factory, tenant.settings)
    assert service.effective_concurrency(2) == 2
    assert service.effective_concurrency(1000) == tenant.settings.batch_max_concurrency

    limited = SharedSettings(batch_max_concurrency=3, batch_platform_limit=1)
    limited_service = BatchFanoutService(tenant.session_factory, limited)
    assert limited_service.effective_concurrency(10) == 1, "platform_limit 更低时取 platform_limit"
    assert limited_service.effective_concurrency(None) == 1
    tmpdir.cleanup()


async def test_b118_parent_waits_without_lease_after_fanout(tenant: TenantContext) -> None:
    """Worker 走真实执行路径：Parent 转 WAITING 且释放 lease，Child 默认 NONE 不主动投递。"""
    _, tmpdir, cache, parent = await _parent_with_batch_plan(tenant)
    executor = _batch_executor(tenant, cache)
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="worker-batch")

    assert await worker.run_once() == parent.id

    refreshed = await fetch_task(tenant, parent.id)
    assert refreshed.status == "WAITING"
    assert refreshed.lease_owner is None and refreshed.lease_until is None
    assert refreshed.task_type == "BATCH"
    assert refreshed.external_ref_json["batch"]["concurrency"] == 2

    children = await _children(tenant, parent.id)
    assert len(children) == 4
    assert all(child.delivery_mode == "NONE" for child in children)
    assert all(child.delivery_status == "NONE" for child in children)
    tmpdir.cleanup()
