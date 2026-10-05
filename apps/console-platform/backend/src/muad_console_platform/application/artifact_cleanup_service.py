"""产物清理（设计 §3.4 `cleanup-artifacts`、RULE-06）。

**判定用两个不同的时间**，这是本模块最容易被做错的地方：

| 判据 | 取自 | 挡的是什么 |
|------|------|-----------|
| **保留期**（默认 30 天） | 行的 `runtime.artifact.create_time` | 正常的历史积累 |
| **宽限期**（默认 1 小时） | 文件的 `st_mtime` | **正在进行中的写入** |

两者不能合并成同一个：保留了 30 天的产物不一定没人正在动它（恢复、重放、外部工具刚碰过），
而"刚写进来的行"天然就不会过期。这也正是 `cleanup-skill-orphans` 的宽限期口径（它对孤儿文件
用 mtime）。**代价**：每个候选要 stat 一次——但候选被 `--limit` 卡住上界，且全程没有"每个文件
一次 DB 往返"那种 N+1。

三类候选的落点：

- **可删**：行过期 + 文件在宽限期外 ⇒ 删文件、删行；
- **宽限期内**：行过期但文件 mtime 很新 ⇒ **跳过**（这轮不动它）;
- **悬空行**：行过期但文件已经不在了 ⇒ 仍要删行——不留悬空行才是可对账的前提。

**为什么有一个 `--tenant`**：默认是全库扫描（运维口径）。加这个限定不是为了好看——一条**没有
租户谓词的破坏性命令根本没法在共享库上做集成验收**：测试只能对着开发库里真实的产物跑，而它会
把不属于本次用例的行一并删掉。这个限制是写测试时被逼出来的，它对运维也成立（按租户清、按租户
对账）。代价：两段 SQL，见 `artifact_retention_repository` 里的说明。

**`storage_key` 越界**（`resolve` 抛 `ValueError`）按**悬空行**处理：那说明行指向的路径不可信，
文件我们也不会去碰（碰不到），但那条行留着的唯一作用就是误导下一个人。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from muad_artifact_store import NfsArtifactStore
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.artifact_retention_repository import (
    ArtifactRetentionRepository,
    ExpiredArtifact,
)

#: 原子写留在盘上的中间文件前缀（`os.replace` 之前的那一份）。
#: **只有进程崩在 `write_bytes` 与 `os.replace` 之间才会残留**——正常路径与失败清理都会消掉它；
#: 残留物也**不影响任何读取**（artifact 行指向的是最终 key），是纯盘上浪费。
TEMP_PREFIX = ".tmp-"

#: 逐条动作的类型（也是 stdout 的行首标记，便于 `grep`/`awk` 对账）。
ACTION_REMOVED = "REMOVED"
ACTION_DANGLING = "DANGLING"
ACTION_SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class CleanupAction:
    """一条可对账的动作。**`storage_key` 打出来是有意的**：运维要核对的正是"哪份字节被删了"，
    这个命令的读者是运维而不是终端用户。"""

    action: str
    artifact_id: uuid.UUID
    tenant_id: str
    storage_key: str
    detail: str = ""

    def line(self) -> str:
        suffix = f" {self.detail}" if self.detail else ""
        return (
            f"{self.action} artifact_id={self.artifact_id} tenant_id={self.tenant_id} "
            f"key={self.storage_key}{suffix}"
        )


@dataclass(frozen=True, slots=True)
class CleanupReport:
    actions: tuple[CleanupAction, ...]
    #: 清掉的临时文件（原子写的残留）。**单独一类**：它们没有 artifact 行、没有租户，
    #: 硬塞进 `CleanupAction` 只能编造出两个字段来。
    temp_files: tuple[str, ...] = ()
    dry_run: bool = False

    @property
    def temp_count(self) -> int:
        return len(self.temp_files)

    def count(self, action: str) -> int:
        return sum(1 for item in self.actions if item.action == action)

    @property
    def scanned(self) -> int:
        return len(self.actions)

    def summary(self) -> str:
        return (
            f"cleanup-artifacts: scanned={self.scanned} "
            f"removed={self.count(ACTION_REMOVED)} "
            f"dangling={self.count(ACTION_DANGLING)} "
            f"skipped={self.count(ACTION_SKIPPED)} "
            f"temp={self.temp_count} "
            f"dry_run={str(self.dry_run).lower()}"
        )


class ArtifactCleanupService:
    def __init__(
        self,
        session: AsyncSession,
        store: NfsArtifactStore,
        *,
        retention_days: int,
        grace_seconds: float,
        limit: int,
        tenant_id: str | None = None,
    ) -> None:
        """`retention_days` / `limit` 由调用方（CLI）从**当次操作边界**取到的平台设置传入
        （`artifact.retention_days` / `artifact.cleanup_batch_size`）——清理不是 Run，不冻结。
        `limit` 保留为**每轮候选上界**：这条命令会删盘上的字节，一次跑太久既不好中断也不好核对。
        """
        self._repository = ArtifactRetentionRepository(session)
        self._store = store
        self._retention = timedelta(days=retention_days)
        self._grace = timedelta(seconds=grace_seconds)
        self._limit = limit
        #: 限定只碰一个租户；`None` = 全库（默认运维口径）。
        self._tenant_id = tenant_id

    async def run(self, *, dry_run: bool, now: datetime | None = None) -> CleanupReport:
        moment = now or datetime.now(UTC)
        rows = await self._repository.expired(
            cutoff=moment - self._retention, limit=self._limit, tenant_id=self._tenant_id
        )
        grace_cutoff = moment - self._grace
        actions: list[CleanupAction] = []
        deletable: list[uuid.UUID] = []
        for row in rows:
            action, path = self._classify(row, grace_cutoff=grace_cutoff)
            actions.append(action)
            if action.action == ACTION_SKIPPED:
                continue
            if path is not None and not dry_run:
                path.unlink(missing_ok=True)
            deletable.append(row.artifact_id)
        if not dry_run:
            await self._repository.delete(deletable)
        temps = self._sweep_temp_files(grace_cutoff=grace_cutoff, dry_run=dry_run)
        return CleanupReport(actions=tuple(actions), temp_files=tuple(temps), dry_run=dry_run)

    def _sweep_temp_files(self, *, grace_cutoff: datetime, dry_run: bool) -> list[str]:
        """清掉原子写的崩溃残留（`.tmp-*`）。

        **复用同一个宽限期**：临时文件是"正在进行中的写入"的痕迹，用删除策略的同一条时间线
        判断，不需要第二个旋钮。`skills/` 那一段**跳过**——`cleanup-skill-orphans` 已经在管它，
        两处都扫只会让运维对不上账。
        """
        root = self._store.root
        removed: list[str] = []
        for path in sorted(root.rglob(f"{TEMP_PREFIX}*")):
            if not path.is_file() or path.relative_to(root).parts[:1] == ("skills",):
                continue
            if datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) > grace_cutoff:
                continue
            if not dry_run:
                path.unlink(missing_ok=True)
            removed.append(str(path.relative_to(root)))
        return removed

    def _classify(
        self, row: ExpiredArtifact, *, grace_cutoff: datetime
    ) -> tuple[CleanupAction, Path | None]:
        try:
            path = self._store.resolve(row.storage_key)
        except ValueError:
            return self._action(ACTION_DANGLING, row, detail="key_out_of_root"), None
        if not path.is_file():
            # 行在、字节没了：本轮把行清掉（这正是"不留悬空行"），但**不碰盘上任何东西**。
            return self._action(ACTION_DANGLING, row, detail="file_missing"), None
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified > grace_cutoff:
            return (
                self._action(ACTION_SKIPPED, row, detail="within_grace_period"),
                path,
            )
        return self._action(ACTION_REMOVED, row, detail=f"size={row.size}"), path

    @staticmethod
    def _action(action: str, row: ExpiredArtifact, *, detail: str) -> CleanupAction:
        return CleanupAction(
            action=action,
            artifact_id=row.artifact_id,
            tenant_id=row.tenant_id,
            storage_key=row.storage_key,
            detail=detail,
        )
