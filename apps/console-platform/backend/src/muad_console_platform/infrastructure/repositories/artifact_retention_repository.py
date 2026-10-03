"""产物保留期的扫描与删除（设计 §3.4 CLI、RULE-06）。

**为什么 console 能碰 `runtime.artifact`**：这是一条**运维/维护路径**，不是请求路径。
console 里已有同类先例——`audit_query_repository` 就 JOIN `runtime.run_record` 做审计聚合。
产物字节落在**共享** artifact store（四单元同挂），所以"删文件"这件事无论谁来做，动的都是
同一份盘。放 console 的理由是**运维只需跑一条 CLI**：`cleanup-skill-orphans` 已经在这里。

**删除顺序刻意是"先文件、后行"**：中途崩了留下的是**悬空行**（有行无文件），而悬空行下一轮
会被判成 `DANGLING` 再清掉——**自愈**。反过来（先删行）留下的是**孤儿文件**：行没了，就再没有
任何记录指向那个 key，谁也认不出它属于谁、该不该删。两个方向都不可原子，选能自愈的那个。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ExpiredArtifact:
    """一条过了保留期的产物行（只取清理要用的列）。"""

    artifact_id: uuid.UUID
    tenant_id: str
    storage_key: str
    size: int
    create_time: datetime


#: 一次**单条**扫描取回候选；`LIMIT` 就是 `--limit`，所以单轮的工作量有上界。
#: 租户谓词是**可选**的（`--tenant`）：不带就是全库扫描（默认的运维口径），带上就只碰一个租户。
#: 两句 SQL 而不是一句带 `(:tenant IS NULL OR ...)` 的：后者会让优化器放弃 `create_time` 上的
#: 索引有序扫描，而这个表的量级正是要靠那条索引撑住的。
_SELECT_EXPIRED = text(
    """
    SELECT id AS artifact_id, tenant_id, storage_key, size, create_time
      FROM runtime.artifact
     WHERE is_deleted = false
       AND create_time < :cutoff
     ORDER BY create_time
     LIMIT :limit
    """
)

_SELECT_EXPIRED_IN_TENANT = text(
    """
    SELECT id AS artifact_id, tenant_id, storage_key, size, create_time
      FROM runtime.artifact
     WHERE is_deleted = false
       AND tenant_id = :tenant_id
       AND create_time < :cutoff
     ORDER BY create_time
     LIMIT :limit
    """
)

#: 批量删行：一次往返删掉本轮的候选，**不做逐行 DELETE**。
_DELETE_ROWS = text("DELETE FROM runtime.artifact WHERE id IN :ids").bindparams(
    bindparam("ids", expanding=True)
)


class ArtifactRetentionRepository:
    """`runtime.artifact` 的保留期读写。**没有 ORM 模型**：这不是 console 拥有的表，
    建模型等于宣称所有权，而这里要的只是"按时间取一批 + 批量删"。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def expired(
        self, *, cutoff: datetime, limit: int, tenant_id: str | None = None
    ) -> list[ExpiredArtifact]:
        if tenant_id is None:
            result = await self._session.execute(
                _SELECT_EXPIRED, {"cutoff": cutoff, "limit": limit}
            )
        else:
            result = await self._session.execute(
                _SELECT_EXPIRED_IN_TENANT,
                {"cutoff": cutoff, "limit": limit, "tenant_id": tenant_id},
            )
        return [
            ExpiredArtifact(
                artifact_id=row.artifact_id,
                tenant_id=row.tenant_id,
                storage_key=row.storage_key,
                size=row.size,
                create_time=row.create_time,
            )
            for row in result
        ]

    async def delete(self, artifact_ids: list[uuid.UUID]) -> None:
        if not artifact_ids:
            return
        await self._session.execute(_DELETE_ROWS, {"ids": artifact_ids})
