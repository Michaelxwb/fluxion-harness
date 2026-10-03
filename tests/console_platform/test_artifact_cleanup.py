"""[TASK-009] 产物清理（S-08 / E-05）：**真实文件系统 + 真实 PG**。

本文件钉的是"删对了、且只删该删的"：

- **S-08**：三个产物（过期 / 过期但文件在宽限期内 / 未过期）⇒ **只**清掉第一个，
  文件与 DB 行**同时**消失，另两个**原地不动**；
- **E-05**：宽限期内的跳过；**行在、文件缺失**按悬空行处理（行清掉、盘上不动），
  且**不误删在用项**；结果可对账（逐条动作 + 汇总）。

**为什么服务要带租户限定**：`cleanup` 默认是全库扫描，而这是一条**破坏性**命令。对着共享的
开发库跑一次全库清理会把不属于本用例的历史产物一并删掉——这条测试根本没法安全地写。所以
`--tenant` 不是锦上添花，是这条命令能被集成验收的**前提**（对运维同样成立：按租户清、按租户对账）。

**不 mock 的两条**：字节真的落在真实文件系统上（`os.utime` 真的把 mtime 拨到过去），
产物行真的落在真 `runtime.artifact`；判定读的也是它们。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import Artifact as RuntimeArtifact
from muad_artifact_store import NfsArtifactStore
from muad_console_platform.application.artifact_cleanup_service import (
    ACTION_DANGLING,
    ACTION_REMOVED,
    ACTION_SKIPPED,
    ArtifactCleanupService,
    CleanupReport,
)
from muad_console_platform.infrastructure.db import get_session_factory

RETENTION_DAYS = 30
GRACE_SECONDS = 3600.0
#: 明显早于保留期（40 天）。用固定偏移而不是"刚好超一天"，避免时钟抖动把边界用例变成 flaky。
EXPIRED_AGE = timedelta(days=40)

PAYLOAD = b"retained artifact bytes\n"
MEDIA_TYPE = "text/plain"


@dataclass(frozen=True)
class Seeded:
    """一个产物：一行 DB + （可选）一份真实字节。"""

    artifact_id: uuid.UUID
    storage_key: str
    path: Path | None


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """本用例自己的 artifact root：真文件系统，但**不碰**开发库那份共享产物目录。"""
    return tmp_path / "artifacts"


@pytest.fixture
async def tenant() -> AsyncIterator[str]:
    """一次性租户：`--tenant` 限定后，本用例只会碰到自己这几行。"""
    yield f"test-cleanup-{uuid.uuid4()}"


def _store(root: Path) -> NfsArtifactStore:
    return NfsArtifactStore(root)


def _write_bytes(root: Path, key: str, *, modified: datetime) -> Path:
    path = _store(root).resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    stamp = modified.timestamp()
    os.utime(path, (stamp, stamp))
    return path


async def _seed_row(*, tenant_id: str, key: str, created: datetime) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            RuntimeArtifact(
                id=artifact_id,
                tenant_id=tenant_id,
                # `task_id` / `run_id` 恰好填一个（表上的 XOR 约束）；选前者可省一条 run。
                task_id=uuid.uuid4(),
                artifact_type="AGENT_OUTPUT",
                storage_key=key,
                media_type=MEDIA_TYPE,
                size=len(PAYLOAD),
                checksum="sha256:" + "0" * 64,
                metadata_json={},
                create_time=created,
                update_time=created,
            )
        )
        await session.commit()
    return artifact_id


async def _seed(
    root: Path, tenant_id: str, *, row_age: timedelta, file_age: timedelta | None
) -> Seeded:
    """落一个产物：行按 `row_age` 老化，文件按 `file_age` 老化（`None` = 不写文件）。"""
    key = f"cleanup/{uuid.uuid4().hex}.txt"
    moment = datetime.now(UTC)
    path = None if file_age is None else _write_bytes(root, key, modified=moment - file_age)
    artifact_id = await _seed_row(tenant_id=tenant_id, key=key, created=moment - row_age)
    return Seeded(artifact_id=artifact_id, storage_key=key, path=path)


async def _row_exists(artifact_id: uuid.UUID) -> bool:
    async with get_session_factory()() as session:
        return (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(RuntimeArtifact)
                .where(RuntimeArtifact.id == artifact_id)
            )
            or 0
        ) > 0


async def _cleanup(
    root: Path,
    tenant_id: str,
    *,
    dry_run: bool = False,
    limit: int = 50,
    retention_days: int = RETENTION_DAYS,
    grace_seconds: float = GRACE_SECONDS,
) -> CleanupReport:
    async with get_session_factory()() as session:
        service = ArtifactCleanupService(
            session,
            _store(root),
            retention_days=retention_days,
            grace_seconds=grace_seconds,
            limit=limit,
            tenant_id=tenant_id,
        )
        report = await service.run(dry_run=dry_run)
        await session.commit()
    return report


def _by_id(report: CleanupReport) -> dict[uuid.UUID, str]:
    return {action.artifact_id: action.action for action in report.actions}


# --------------------------------------------------------------------------- S-08


async def test_s08_only_the_expired_artifact_is_removed(root: Path, tenant: str) -> None:
    """S-08：三个产物里**只有过期那个**被清理，文件与 DB 行同时消失，另两个原地不动。"""
    expired = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=EXPIRED_AGE)
    in_grace = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=timedelta(0))
    in_use = await _seed(root, tenant, row_age=timedelta(minutes=5), file_age=timedelta(minutes=5))

    report = await _cleanup(root, tenant)

    assert _by_id(report) == {
        expired.artifact_id: ACTION_REMOVED,
        in_grace.artifact_id: ACTION_SKIPPED,
    }, f"只应有「过期」与「宽限期内」两条动作，实际 {report.actions!r}"

    # 过期项：文件与行**同时**消失
    assert expired.path is not None and not expired.path.exists(), "过期文件必须被删掉"
    assert not await _row_exists(expired.artifact_id), "过期行必须被删掉"

    # 宽限期内：两边都原地不动（这正是"保护进行中的写入"）
    assert in_grace.path is not None and in_grace.path.exists(), "宽限期内的文件不得被动"
    assert await _row_exists(in_grace.artifact_id), "宽限期内的行不得被动"

    # 在用：连候选都不该进（行没过期）
    assert in_use.path is not None and in_use.path.exists()
    assert await _row_exists(in_use.artifact_id)
    assert in_use.artifact_id not in _by_id(report), "没过期的产物不该出现在任何动作里"


async def test_s08_dry_run_reports_without_deleting_anything(root: Path, tenant: str) -> None:
    """`--dry-run` 先出清单：报出来的和真跑一样，但**一个字节、一行都不动**。"""
    expired = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=EXPIRED_AGE)

    report = await _cleanup(root, tenant, dry_run=True)

    assert report.dry_run is True
    assert _by_id(report) == {expired.artifact_id: ACTION_REMOVED}
    assert expired.path is not None and expired.path.exists(), "dry-run 不得删文件"
    assert await _row_exists(expired.artifact_id), "dry-run 不得删行"
    assert "dry_run=true" in report.summary()


async def test_s08_limit_bounds_one_run_and_the_rest_is_picked_up_next_time(
    root: Path, tenant: str
) -> None:
    """`--limit` 是**单轮上界**：一次只清 `limit` 个，剩下的下一轮继续（运维重复执行控节奏）。"""
    seeds = [
        await _seed(root, tenant, row_age=EXPIRED_AGE + timedelta(days=index), file_age=EXPIRED_AGE)
        for index in range(3)
    ]

    first = await _cleanup(root, tenant, limit=2)
    second = await _cleanup(root, tenant, limit=2)

    assert first.count(ACTION_REMOVED) == 2
    assert second.count(ACTION_REMOVED) == 1
    for seed in seeds:
        assert not await _row_exists(seed.artifact_id), f"第一轮 + 第二轮应把三行都清掉：{seed}"
        assert seed.path is not None and not seed.path.exists()


# --------------------------------------------------------------------------- E-05


async def test_e05_dangling_row_is_cleaned_without_touching_the_disk(
    root: Path, tenant: str
) -> None:
    """E-05：**有行、没文件** ⇒ 按悬空行处理：行清掉（不留悬空行），盘上什么都不动。

    "不动盘"这句是**断言得出来的**——同租户里还有一个在用文件，它必须活下来。
    少了这条，一个"顺手把目录清空"的实现也能让它变绿。
    """
    dangling = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=None)
    bystander = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=timedelta(0))

    report = await _cleanup(root, tenant)

    assert _by_id(report) == {
        dangling.artifact_id: ACTION_DANGLING,
        bystander.artifact_id: ACTION_SKIPPED,
    }
    assert not await _row_exists(dangling.artifact_id), "悬空行要清掉"
    assert bystander.path is not None and bystander.path.exists(), "清理不得误伤别人的文件"
    assert await _row_exists(bystander.artifact_id), "清理不得误伤在用的行"


async def test_e05_result_is_reconcilable_line_by_line(root: Path, tenant: str) -> None:
    """E-05：结果**可对账** —— 逐条动作带得出 `artifact_id` 与 `storage_key`，汇总数与条目一致。

    运维核对的对象就是这些行：哪份字节被删了、哪条被跳过了、为什么。汇总里的数字必须能从
    逐条行里数出来，否则它只是一句没人能验证的自述。
    """
    removed = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=EXPIRED_AGE)
    skipped = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=timedelta(0))
    dangling = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=None)

    report = await _cleanup(root, tenant)

    lines = [action.line() for action in report.actions]
    assert len(lines) == 3
    assert sum(1 for line in lines if line.startswith(ACTION_REMOVED)) == 1
    for action in report.actions:
        assert str(action.artifact_id) in action.line()
        assert action.storage_key in action.line(), "对账要看得出删的是哪个 key"
    assert removed.storage_key in next(
        line for line in lines if line.startswith(ACTION_REMOVED)
    )
    summary_line = report.summary()
    assert summary_line.startswith("cleanup-artifacts: scanned=3 ")
    assert "removed=1" in summary_line
    assert "dangling=1" in summary_line
    assert "skipped=1" in summary_line
    assert dangling.artifact_id in _by_id(report)
    assert skipped.artifact_id in _by_id(report)


async def test_e05_a_second_run_finds_nothing_left(root: Path, tenant: str) -> None:
    """幂等：清完再跑一次**为空**（已删的行不再出现、没删的仍不被删）。

    这条是"文件与行同时消失"的另一种说法——只要有一边落下，第二轮就会冒出动作。
    """
    await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=EXPIRED_AGE)
    in_grace = await _seed(root, tenant, row_age=EXPIRED_AGE, file_age=timedelta(0))

    await _cleanup(root, tenant)
    second = await _cleanup(root, tenant)

    assert second.count(ACTION_REMOVED) == 0, "第一轮已清干净，第二轮不该再有可删项"
    assert _by_id(second) == {in_grace.artifact_id: ACTION_SKIPPED}, (
        "宽限期内那份**仍在**被保护——第二轮不该顺手把它带上"
    )

    third = await _cleanup(root, tenant, grace_seconds=0.0)
    assert _by_id(third) == {in_grace.artifact_id: ACTION_REMOVED}, (
        "宽限期放开后原本被保护的那份才轮到——反过来说明第一轮它确实是被**跳过**的，而不是被漏掉的"
    )


async def test_temp_leftovers_are_swept_with_the_same_grace_period(root: Path, tenant: str) -> None:
    """原子写的崩溃残留（`.tmp-*`）**复用同一个宽限期**清掉，且不碰 `skills/`。

    残留只在进程崩在 `write_bytes` 与 `os.replace` 之间时产生——它**不影响任何读取**
    （artifact 行指向最终 key），是纯盘上浪费；但没有回收器就永远躺在 PVC 上。
    宽限期与删除策略共用一条时间线，不新增第二个旋钮。
    `skills/` 那一段跳过：`cleanup-skill-orphans` 已经在管，两处都扫会让运维对不上账。
    """
    stale = root / "outbound" / "run-1" / ".tmp-stale"
    fresh = root / "outbound" / "run-2" / ".tmp-fresh"
    skill_tmp = root / "skills" / "t" / "s" / ".tmp-skill"
    for path, age in ((stale, EXPIRED_AGE), (fresh, timedelta(0)), (skill_tmp, EXPIRED_AGE)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"partial")
        stamp = (datetime.now(UTC) - age).timestamp()
        os.utime(path, (stamp, stamp))

    report = await _cleanup(root, tenant)

    assert not stale.exists(), "超过宽限期的临时文件必须清掉"
    assert "outbound/run-1/.tmp-stale" in report.temp_files, "对账要看得见清掉了哪个"
    assert fresh.exists(), "宽限期内的临时文件不得被动（可能正在写）"
    assert skill_tmp.exists(), "skills/ 归 cleanup-skill-orphans 管，这里不得重复处理"


async def test_dry_run_does_not_delete_temp_leftovers(root: Path, tenant: str) -> None:
    """`--dry-run` 只报告，临时文件一个都不删。"""
    stale = root / "outbound" / "run-9" / ".tmp-x"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"partial")
    stamp = (datetime.now(UTC) - EXPIRED_AGE).timestamp()
    os.utime(stale, (stamp, stamp))

    report = await _cleanup(root, tenant, dry_run=True)

    assert stale.exists(), "dry-run 不得删文件"
    assert "outbound/run-9/.tmp-x" in report.temp_files, "但要报出来"
