"""L2 legacy 迁移（closure TASK-009 / M202）。

session_memory 中 level='l2' 的 legacy user-raw 记录（停双写遗留）一次性迁移：
audit_l2() 只读 dry-run 报告（行数/tenant 分类）；migrate_l2() 执行迁移——
l2 记录转为 episodic 语义迁入 personal_memory（经 MemoryLearner 语义，直接落表
learning_enabled=True），随后删除 l2 行。幂等：二次执行零变更。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import personal_memory, session_memory


@dataclass(frozen=True, slots=True)
class L2AuditReport:
    """dry-run 报告：行数 + tenant 分类。"""

    total: int
    tenants: dict[str, int]


@dataclass(frozen=True, slots=True)
class L2MigrationResult:
    migrated: int
    deleted: int


async def audit_l2(engine: AsyncEngine) -> L2AuditReport:
    """只读扫描 session_memory 中 level=l2 的 legacy 行。"""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(session_memory.c.tenant_id)
                .where(session_memory.c.level == "l2")
                .order_by(session_memory.c.tenant_id.asc())
            )
        ).all()
    tenants: dict[str, int] = {}
    for (tenant_id,) in rows:
        tenants[tenant_id] = tenants.get(tenant_id, 0) + 1
    return L2AuditReport(total=len(rows), tenants=tenants)


async def migrate_l2(engine: AsyncEngine) -> L2MigrationResult:
    """l2 legacy 记录迁入 personal_memory（episodic 语义）后删除原行；幂等。"""
    report = await audit_l2(engine)
    if report.total == 0:
        return L2MigrationResult(migrated=0, deleted=0)
    migrated = 0
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                select(
                    session_memory.c.tenant_id,
                    session_memory.c.session_id,
                    session_memory.c.content,
                ).where(session_memory.c.level == "l2")
            )
        ).mappings().all()
        for row in rows:
            await conn.execute(
                insert(personal_memory).values(
                    tenant_id=row["tenant_id"],
                    user_id=_user_from_session(row["session_id"]),
                    memory_type="episodic",
                    content=str(row["content"]),
                    source_session_id=row["session_id"],
                    source_range_hash=None,
                    learning_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        await conn.execute(delete(session_memory).where(session_memory.c.level == "l2"))
        migrated = len(rows)
    return L2MigrationResult(migrated=migrated, deleted=report.total)


def _user_from_session(session_id: str) -> str:
    """legacy l2 行无独立 user 列——按 session 前缀约定回推（migration:session）。"""
    return f"migration:{session_id}"
