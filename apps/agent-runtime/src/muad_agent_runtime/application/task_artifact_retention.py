"""Task 产物的保留清理（design §3.1.5 owner、§3.5.11 保留；TASK-031）。

**为什么这条清理归 Runtime**：`runtime.artifact` 的 owner 是 Runtime，字节落在共享 artifact
store（四单元同挂）。归属在写入方已经固定（`task_artifacts.write_task_artifact` 按
`task.task_execution` 复核），回收也必须由同一个 owner 做——Worker 只读该表、不跨 schema
直写。

**跟随执行保留期**：候选是 `task_id` 非空（Task-owned）且 `create_time` 早于执行保留边界的
产物行。`run_id` 非空的 Run 产物**永不进候选**——那是 Run 的生命周期，不归这里管。

**还在用的不删**：`task_id` 指向的执行仍非终态（`QUEUED/RUNNING/WAITING_EXTERNAL/
WAITING_HUMAN`）时整行保留：等待中的执行还要恢复、重投，结果产物就是它要交给用户的字节。
执行行已经不存在（历史已被 Worker 清理）则不再有引用方，按行龄回收。

**先文件、后行**：每个产物的所有不可变版本键先删字节，再删行。中途崩了留下的是**悬空行**
（有行无文件），下一轮会再被判成"文件缺失"并清掉行——**自愈**；反过来先删行留下的是孤儿
文件，再没有任何记录指向它。两个方向都不可原子，选能自愈的那个（与 Console 产物清理同口径）。

**失败显式且可重试**：删字节失败（`OSError`）**不删行**，记 warning 并计入报告的 `failed`，
下一轮还是候选；不再有"静默跳过"。键越界（`ValueError`）按悬空行处理：行指向的路径不可信，
字节也不碰。

**锁外做文件 IO**：候选选择、删字节、删行是三段分开的短事务——字节删除发生在两次数据库事务
之间，不持任何行锁；单批行数有上界，跨批用 `(create_time, id)` 前进游标。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
from muad_artifact_store import NfsArtifactStore
from muad_contracts import TaskStatus
from muad_logging import get_logger
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.db_clock import database_now

logger = get_logger(__name__)

#: 单批候选上界：破坏性维护动作，有界前进才可中断、可对账。
TASK_ARTIFACT_RETENTION_BATCH_SIZE = 200
#: 一轮最多推进几批：文件 IO 与数据库往返都要有上界。
TASK_ARTIFACT_RETENTION_MAX_BATCHES = 5

#: 仍需要恢复/重投的执行状态：命中的产物整行保留（与 Console 产物清理同口径）。
NON_TERMINAL_TASK_STATUSES: tuple[str, ...] = (
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_EXTERNAL.value,
    TaskStatus.WAITING_HUMAN.value,
)

Position = tuple[datetime, uuid.UUID]

_SELECT_CANDIDATES = """
    SELECT a.id AS artifact_id, a.storage_key, a.metadata_json, a.create_time
      FROM runtime.artifact a
     WHERE a.tenant_id = :tenant_id
       AND a.is_deleted = false
       AND a.task_id IS NOT NULL
       AND a.create_time < :cutoff
       AND NOT EXISTS (
             SELECT 1
               FROM task.task_execution t
              WHERE t.id = a.task_id
                AND t.is_deleted = false
                AND t.status IN :non_terminal
           ){cursor}
     ORDER BY a.create_time, a.id
     LIMIT :limit
"""

#: 两句而不是一句带 `(:cursor_time IS NULL OR ...)` 的：后者让优化器放弃 `(create_time, id)`
#: 的有序索引扫描，而这个表的量级正是靠那条索引撑住的（与 Console 产物清理同一取舍）。
_SELECT_FIRST_BATCH = sa.text(
    _SELECT_CANDIDATES.format(cursor="")
).bindparams(sa.bindparam("non_terminal", expanding=True))

_SELECT_AFTER_CURSOR = sa.text(
    _SELECT_CANDIDATES.format(
        cursor="\n       AND (a.create_time, a.id) > (:cursor_time, :cursor_id)"
    )
).bindparams(sa.bindparam("non_terminal", expanding=True))

_DELETE_ROWS = sa.text("DELETE FROM runtime.artifact WHERE id IN :ids").bindparams(
    sa.bindparam("ids", expanding=True)
)


@dataclass(frozen=True, slots=True)
class TaskArtifactRetentionReport:
    removed: int = 0
    failed: int = 0
    inspected: int = 0
    cursor_exhausted: bool = True

    def summary(self) -> str:
        return (
            f"task-artifact-retention: removed={self.removed} failed={self.failed} "
            f"inspected={self.inspected} exhausted={str(self.cursor_exhausted).lower()}"
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    artifact_id: uuid.UUID
    storage_key: str
    metadata_json: dict[str, Any]
    create_time: datetime


def version_keys(artifact_id: uuid.UUID, storage_key: str, metadata_json: Any) -> list[str]:
    """一个产物行的**全部**不可变版本键：当前版 + `metadata_json.versions[].key`。

    只删当前键会把手写历史版本留在盘上——行没了就再没有记录指向它们。
    """
    keys: list[str] = []
    if isinstance(storage_key, str) and storage_key:
        keys.append(storage_key)
    versions = (metadata_json or {}).get("versions") if isinstance(metadata_json, dict) else None
    if isinstance(versions, list):
        for entry in versions:
            key = entry.get("key") if isinstance(entry, dict) else None
            if isinstance(key, str) and key and key not in keys:
                keys.append(key)
    if not keys:
        logger.warning("task_artifact_row_without_key artifact_id=%s", artifact_id)
    return keys


class TaskArtifactRetentionService:
    """有界回收 Task-owned 产物；**事务由调用方持有**，文件 IO 在两次事务之间。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, artifact_root: str | Path
    ) -> None:
        self._session_factory = session_factory
        self._store = NfsArtifactStore(artifact_root)

    async def run(
        self,
        *,
        tenant_id: str,
        execution_history_days: int,
        batch_size: int = TASK_ARTIFACT_RETENTION_BATCH_SIZE,
        max_batches: int = TASK_ARTIFACT_RETENTION_MAX_BATCHES,
        now: datetime | None = None,
    ) -> TaskArtifactRetentionReport:
        if isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("invalid task artifact retention batch size")
        if isinstance(max_batches, bool) or max_batches < 1:
            raise ValueError("invalid task artifact retention batch count")
        if not self._store.root.is_dir():
            # 根没挂上时 `unlink(missing_ok=True)` 会"成功"：删了行却没碰字节＝真丢数据。
            # 这条清理没有运维在旁边看，宁可显式失败（循环会记 failed + exception）也不静默跳过。
            raise RuntimeError(f"artifact root is not a directory: {self._store.root}")
        moment = now if now is not None else await self._database_now()
        cutoff = moment - timedelta(days=execution_history_days)
        report = TaskArtifactRetentionReport(cursor_exhausted=False)
        cursor: Position | None = None
        for _ in range(max_batches):
            candidates = await self._candidates(
                tenant_id=tenant_id, cutoff=cutoff, cursor=cursor, limit=batch_size
            )
            if len(candidates) < batch_size:
                # 不满一批 = 这一轮已经扫到末尾（没有更多候选，或剩下的都被保护谓词挡住）。
                report = replace(report, cursor_exhausted=True)
            removed_ids = await self._remove_files(candidates)
            deleted = await self._delete_rows(removed_ids)
            report = replace(
                report,
                removed=report.removed + deleted,
                failed=report.failed + (len(candidates) - len(removed_ids)),
                inspected=report.inspected + len(candidates),
            )
            if report.cursor_exhausted or not candidates:
                break
            cursor = (candidates[-1].create_time, candidates[-1].artifact_id)
        if report.removed or report.failed:
            logger.info("task_artifact_retention_removed", extra={"summary": report.summary()})
        return report

    async def _database_now(self) -> datetime:
        async with self._session_factory() as session:
            return await database_now(session)

    async def _candidates(
        self, *, tenant_id: str, cutoff: datetime, cursor: Position | None, limit: int
    ) -> list[_Candidate]:
        statement = _SELECT_FIRST_BATCH if cursor is None else _SELECT_AFTER_CURSOR
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "cutoff": cutoff,
            "non_terminal": list(NON_TERMINAL_TASK_STATUSES),
            "limit": limit,
        }
        if cursor is not None:
            params.update(cursor_time=cursor[0], cursor_id=cursor[1])
        async with self._session_factory() as session:
            rows = (await session.execute(statement, params)).all()
        return [
            _Candidate(
                artifact_id=row.artifact_id,
                storage_key=row.storage_key,
                metadata_json=dict(row.metadata_json or {}),
                create_time=row.create_time,
            )
            for row in rows
        ]

    async def _remove_files(self, candidates: list[_Candidate]) -> list[uuid.UUID]:
        """删本批的单节；**没有字节可删**（文件本来就不在）也算成功——行才是残留。"""
        removed: list[uuid.UUID] = []
        for candidate in candidates:
            keys = version_keys(
                candidate.artifact_id, candidate.storage_key, candidate.metadata_json
            )
            try:
                await asyncio.to_thread(self._unlink_all, keys)
            except OSError as exc:
                logger.warning(
                    "task_artifact_unlink_failed artifact_id=%s error_type=%s",
                    candidate.artifact_id,
                    type(exc).__name__,
                )
                continue
            removed.append(candidate.artifact_id)
        return removed

    def _unlink_all(self, keys: list[str]) -> None:
        """键越界视同文件不存在（行不可信，字节也不碰）；其余 IO 失败向上抛、留给下一轮。"""
        for key in keys:
            try:
                path = self._store.resolve(key)
            except ValueError:
                logger.warning("task_artifact_key_out_of_root key=%s", key)
                continue
            path.unlink(missing_ok=True)

    async def _delete_rows(self, artifact_ids: list[uuid.UUID]) -> int:
        if not artifact_ids:
            return 0
        async with self._session_factory() as session, session.begin():
            result = await session.execute(_DELETE_ROWS, {"ids": artifact_ids})
        return int(cast(CursorResult[Any], result).rowcount or 0)


async def run_task_artifact_retention(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    artifact_root: str | Path,
    tenant_id: str,
    execution_history_days: int,
    batch_size: int = TASK_ARTIFACT_RETENTION_BATCH_SIZE,
    max_batches: int = TASK_ARTIFACT_RETENTION_MAX_BATCHES,
    now: datetime | None = None,
) -> TaskArtifactRetentionReport:
    """装配点：Runtime 的保留循环调用一次即一轮（有界批 + 前进游标）。"""
    return await TaskArtifactRetentionService(
        session_factory, artifact_root=artifact_root
    ).run(
        tenant_id=tenant_id,
        execution_history_days=execution_history_days,
        batch_size=batch_size,
        max_batches=max_batches,
        now=now,
    )


__all__ = [
    "NON_TERMINAL_TASK_STATUSES",
    "TASK_ARTIFACT_RETENTION_BATCH_SIZE",
    "TASK_ARTIFACT_RETENTION_MAX_BATCHES",
    "TaskArtifactRetentionReport",
    "TaskArtifactRetentionService",
    "run_task_artifact_retention",
    "version_keys",
]
