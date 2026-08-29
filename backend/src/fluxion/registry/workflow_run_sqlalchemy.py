"""`workflow_run` 投影表 async CRUD（TASK-008 / FEAT-P3-06，design §3.3）。

Repository 层：业务代码只调这些函数（RULE-backend-database-001 "CRUD 抽象层"），
禁止在 service / handler 直写 ORM 查询。全部参数化，无 N+1（node_states 是单
JSON 列，整批读取/写入，PATTERN-backend-003）。

写入方是 DBOS worker 进程（`runtime/workflow_projection.py` 的 psycopg writer，
同表跨驱动）；本模块是 API/Console 读路径 + 双库契约的 async SQLAlchemy 侧。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.dml import Insert

from fluxion.registry.schema import workflow_run


def _upsert_statement(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    run_id: str,
    workflow_id: str,
    workflow_version: int,
    execution_id: str,
    trace_id: str,
    pinned_refs: list[dict[str, str]],
    status: str = "running",
    node_states: dict[str, object] | None = None,
) -> Insert:
    """跨 dialect INSERT ... ON CONFLICT（sqlite + pg 双库契约）幂等 upsert。"""
    values: dict[str, object] = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "execution_id": execution_id,
        "trace_id": trace_id,
        "status": status,
        "pinned_refs": pinned_refs,
        "node_states": node_states,
        "updated_at": func.now(),
    }
    if engine.dialect.name == "postgresql":
        return postgresql_insert(workflow_run).values(**values).on_conflict_do_update(
            index_elements=[workflow_run.c.tenant_id, workflow_run.c.run_id],
            set_={
                "status": status,
                "node_states": node_states,
                "pinned_refs": pinned_refs,
                "updated_at": func.now(),
            },
        )
    return sqlite_insert(workflow_run).values(**values).on_conflict_do_update(
        index_elements=[workflow_run.c.tenant_id, workflow_run.c.run_id],
        set_={
            "status": status,
            "node_states": node_states,
            "pinned_refs": pinned_refs,
            "updated_at": func.now(),
        },
    )


async def upsert_workflow_run(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    run_id: str,
    workflow_id: str,
    workflow_version: int,
    execution_id: str,
    trace_id: str,
    pinned_refs: list[dict[str, str]],
    status: str = "running",
    node_states: dict[str, object] | None = None,
) -> None:
    """幂等 upsert run 投影行（解释器/worker 写路径；replay/重复调用安全）。"""
    statement = _upsert_statement(
        engine,
        tenant_id=tenant_id,
        run_id=run_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        execution_id=execution_id,
        trace_id=trace_id,
        pinned_refs=pinned_refs,
        status=status,
        node_states=node_states,
    )
    async with engine.begin() as connection:
        await connection.execute(statement)


async def get_workflow_run(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    run_id: str,
) -> RowMapping | None:
    """按 (tenant_id, run_id) 精确读取（tenant scope 全链路，rule 16）。"""
    statement = (
        select(workflow_run)
        .where(workflow_run.c.tenant_id == tenant_id)
        .where(workflow_run.c.run_id == run_id)
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().first()
    return row


async def list_workflow_runs(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    workflow_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[RowMapping], int]:
    """tenant 强制 scope 的 workflow run 列表（count + rows 一次取回，无 N+1）。

    workflow_id=None → 跨工作流 list-all（Phase 5 TASK-011，S-12）。
    """
    base = select(workflow_run).where(workflow_run.c.tenant_id == tenant_id)
    if workflow_id is not None:
        base = base.where(workflow_run.c.workflow_id == workflow_id)
    count_statement = select(func.count()).select_from(base.subquery())
    rows_statement = (
        base.order_by(workflow_run.c.created_at.desc(), workflow_run.c.run_id)
        .limit(limit)
        .offset(offset)
    )
    async with engine.connect() as connection:
        total = int((await connection.execute(count_statement)).scalar_one())
        rows = (await connection.execute(rows_statement)).mappings().all()
    return list(rows), total
