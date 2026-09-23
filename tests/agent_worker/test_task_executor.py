"""按 Task 冻结 Snapshot 执行 Skill 的 worker 执行器（B-112 / RULE-skill-001）。

真实边界：真实 NFS 目录 → emptyDir 本地缓存（checksum 校验 + READY）→ 真实 Skill
子进程执行。不 mock 文件系统，也不从 NFS 直接执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest
from conftest import TenantContext
from helpers import persist_task
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache

SKILL_MAIN = (
    "import json, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "print(json.dumps({'echo': payload, 'skill': 'policy_check'}))\n"
)

STORAGE_KEY = "skills/policy_check.zip"
ARTIFACT_ID = "7f4a3c1e-0000-4000-8000-000000000001"
ARTIFACT_UUID = uuid.UUID(ARTIFACT_ID)


def _build_skill_zip(root: Path, *, body: str = SKILL_MAIN) -> str:
    """在 NFS 根下放一个技能包，返回其 sha256 校验和。"""
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body, encoding="utf-8")
        archive_path = root / STORAGE_KEY
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((root / STORAGE_KEY).read_bytes()).hexdigest()


class CountingStore(NfsArtifactStore):
    """统计 NFS 读取次数，用来观测 singleflight。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.resolve_calls = 0

    def resolve(self, storage_key: str) -> Path:
        self.resolve_calls += 1
        return super().resolve(storage_key)


def _caches(tmp: Path) -> tuple[CountingStore, SkillArtifactCache]:
    store = CountingStore(tmp / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    return store, SkillArtifactCache(store, tmp / "cache")


def _snapshot(checksum: str, *, storage_key: str = STORAGE_KEY) -> dict[str, object]:
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
                "storage_key": storage_key,
                "execution_mode": "ASYNC",
            }
        ],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"max_tokens": 1024},
    }


async def test_executes_frozen_artifact_from_local_ready_cache(
    tenant: TenantContext,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        checksum = _build_skill_zip(store.root)

        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
            input_json={"customers": ["A"]},
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    assert outcome["status"] == "SUCCEEDED", outcome
    assert outcome["result"]["echo"] == {"customers": ["A"]}
    assert outcome["result"]["skill"] == "policy_check"


async def test_execution_path_is_local_cache_not_nfs(tenant: TenantContext) -> None:
    """执行目录必须是本地 READY 缓存，不能是 NFS 挂载路径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        checksum = _build_skill_zip(store.root)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
        )

        ready_dir = await cache.ensure(
            artifact_id=ARTIFACT_ID, storage_key=STORAGE_KEY, checksum=checksum
        )
        outcome = await SkillTaskExecutor(cache).execute(task)

        assert (ready_dir / "READY").is_file()
        assert str(ready_dir.resolve()).startswith(str((tmp / "cache").resolve()))
        assert not str(ready_dir.resolve()).startswith(str(store.root))

    assert outcome["status"] == "SUCCEEDED"


async def test_checksum_mismatch_is_rejected(tenant: TenantContext) -> None:
    """校验和不匹配必须拒绝执行，且不产生 READY 目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        _build_skill_zip(store.root)
        wrong = "sha256:" + "0" * 64
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(wrong),
        )

        with pytest.raises(TaskExecutionError) as excinfo:
            await SkillTaskExecutor(cache).execute(task)

    assert excinfo.value.code == "SKILL_ARTIFACT_CHECKSUM_MISMATCH"


async def test_same_checksum_concurrent_prepare_is_singleflight(
    tenant: TenantContext,
) -> None:
    """同一 checksum 并发首次加载只解包一次。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        checksum = _build_skill_zip(store.root)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
        )
        executor = SkillTaskExecutor(cache)

        outcomes = await asyncio.gather(*(executor.execute(task) for _ in range(5)))

    assert all(item["status"] == "SUCCEEDED" for item in outcomes)
    assert store.resolve_calls == 1, f"NFS 被读取 {store.resolve_calls} 次，singleflight 失效"


async def test_unknown_artifact_is_rejected(tenant: TenantContext) -> None:
    """NFS 上不存在该 storage_key 时明确失败，而不是静默跑空。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _store, cache = _caches(tmp)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot("sha256:" + "a" * 64, storage_key="skills/missing.zip"),
        )

        with pytest.raises(TaskExecutionError) as excinfo:
            await SkillTaskExecutor(cache).execute(task)

    assert excinfo.value.code == "SKILL_ARTIFACT_UNAVAILABLE"


async def test_missing_frozen_skill_in_snapshot_is_rejected(tenant: TenantContext) -> None:
    """快照里没有冻结的 skill 条目时拒绝执行（不允许回退到 current 定义）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _store, cache = _caches(tmp)
        snapshot = _snapshot("sha256:" + "a" * 64)
        snapshot["skills"] = []
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=snapshot,
        )

        with pytest.raises(TaskExecutionError) as excinfo:
            await SkillTaskExecutor(cache).execute(task)

    assert excinfo.value.code == "SKILL_SNAPSHOT_MISSING"


async def test_failing_skill_reports_failure(tenant: TenantContext) -> None:
    """技能进程非零退出时返回 FAILED，而不是抛出未分类异常。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        checksum = _build_skill_zip(store.root, body="import sys\nsys.exit(3)\n")
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    assert outcome["status"] == "FAILED"
    assert outcome["exit_code"] == 3


async def test_child_env_does_not_leak_secrets(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子进程环境只放 allowlist，模型密钥等不出现在技能进程里。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        body = (
            "import json, os, sys\n"
            "print(json.dumps({'has_key': 'MODEL_API_KEY' in os.environ}))\n"
        )
        checksum = _build_skill_zip(store.root, body=body)
        monkeypatch.setenv("MODEL_API_KEY", "sk-secret")
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    assert outcome["status"] == "SUCCEEDED", outcome
    assert outcome["result"]["has_key"] is False
    assert "sk-secret" not in json.dumps(outcome)


async def test_executes_skill_matching_task_artifact_not_first_entry(
    tenant: TenantContext,
) -> None:
    """快照里有多个 Skill 时按 skill_artifact_id 选中目标，不能取第一个。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        checksum = _build_skill_zip(store.root)
        snapshot = _snapshot(checksum)
        decoy = dict(snapshot["skills"][0])  # type: ignore[index]
        decoy.update(
            artifact_id=str(uuid.uuid4()),
            key="other_skill",
            storage_key="skills/other.zip",
            checksum="sha256:" + "b" * 64,
        )
        snapshot["skills"] = [decoy, snapshot["skills"][0]]  # type: ignore[index]
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=snapshot,
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    assert outcome["status"] == "SUCCEEDED", outcome
    assert outcome["result"]["skill"] == "policy_check"


async def test_snapshot_without_task_artifact_is_rejected(tenant: TenantContext) -> None:
    """快照里只有别的 Skill 时拒绝执行，而不是执行错对象。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _store, cache = _caches(Path(tmpdir))
        task = await persist_task(
            tenant,
            skill_artifact_id=uuid.uuid4(),
            execution_snapshot_json=_snapshot("sha256:" + "a" * 64),
        )

        with pytest.raises(TaskExecutionError) as excinfo:
            await SkillTaskExecutor(cache).execute(task)

    assert excinfo.value.code == "SKILL_SNAPSHOT_MISSING"


async def test_skill_receives_task_context_env(tenant: TenantContext) -> None:
    """Skill 通过环境变量拿到 task_id/幂等键/attempt/上轮 external_ref，用于副作用防重与轮询续接。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store, cache = _caches(tmp)
        body = (
            "import json, os, sys\n"
            "print(json.dumps({key: os.environ.get(key) for key in "
            "('MUAD_TASK_ID', 'MUAD_TASK_IDEMPOTENCY_KEY', "
            "'MUAD_TASK_ATTEMPT', 'MUAD_TASK_EXTERNAL_REF')}))\n"
        )
        checksum = _build_skill_zip(store.root, body=body)
        task = await persist_task(
            tenant,
            skill_artifact_id=ARTIFACT_UUID,
            execution_snapshot_json=_snapshot(checksum),
            idempotency_key="run:r1:skill:policy_check:abc",
            attempt=2,
            external_ref_json={"external_task_id": "ext-7"},
        )

        outcome = await SkillTaskExecutor(cache).execute(task)

    result = outcome["result"]
    assert result["MUAD_TASK_ID"] == str(task.id)
    assert result["MUAD_TASK_IDEMPOTENCY_KEY"] == "run:r1:skill:policy_check:abc"
    assert result["MUAD_TASK_ATTEMPT"] == "2"
    assert json.loads(result["MUAD_TASK_EXTERNAL_REF"]) == {"external_task_id": "ext-7"}
