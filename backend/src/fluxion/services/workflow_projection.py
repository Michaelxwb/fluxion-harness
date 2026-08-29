"""`workflow_run` 投影查询服务（TASK-008 / FEAT-P3-06，design §3.4）。

Console-facing 读路径：`get_run`/`list_runs` 全链 tenant scope（rule 16 /
RULE-P3-06），不存在/跨租户统一 `ConsoleResourceNotFoundError`（E-02 404 +
统一 envelope）。读 path 走 registry async CRUD（`SQLAlchemyRegistryStore`
具体方法，未扩展 Protocol——rule 25）；写 path 是 worker 进程 psycopg writer
（`runtime/workflow_projection.py`），同表跨驱动，双库契约保证 schema 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fluxion.contracts.workflow import WorkflowEngine, WorkflowExecutionHistory
from fluxion.errors.console import ConsoleResourceNotFoundError
from fluxion.errors.workflow import WorkflowRunNotFoundError
from fluxion.registry.sqlalchemy_store import SQLAlchemyRegistryStore


@dataclass(frozen=True, slots=True)
class WorkflowRunProjection:
    """单 run 投影（design §3.3 字段 + API 返回的 node_states / pinned_refs）。"""

    run_id: str
    tenant_id: str
    workflow_id: str
    workflow_version: int
    execution_id: str
    trace_id: str
    status: str
    node_states: dict[str, object]
    pinned_refs: list[dict[str, str]]
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkflowRunDetail:
    """单 run 详情：投影 + execution history（S-11 返回体）。"""

    projection: WorkflowRunProjection
    execution_history: WorkflowExecutionHistory | None


@dataclass(frozen=True, slots=True)
class WorkflowRunPage:
    items: tuple[WorkflowRunProjection, ...]
    total: int


def _row_to_projection(row: Any) -> WorkflowRunProjection:
    return WorkflowRunProjection(
        run_id=str(row["run_id"]),
        tenant_id=str(row["tenant_id"]),
        workflow_id=str(row["workflow_id"]),
        workflow_version=int(row["workflow_version"]),
        execution_id=str(row["execution_id"]),
        trace_id=str(row["trace_id"]),
        status=str(row["status"]),
        node_states=dict(row["node_states"] or {}),
        pinned_refs=list(row["pinned_refs"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class WorkflowProjectionService:
    def __init__(
        self,
        store: SQLAlchemyRegistryStore,
        *,
        workflow_engine: WorkflowEngine | None = None,
    ) -> None:
        self._store = store
        # 可选 engine：注入则 `get_run_with_history` 组装 execution history（DBOSClient
        # 免 launch 读路径，Runtime 边界不内侵）；未注入则只返回投影。
        self._workflow_engine = workflow_engine

    async def get_run(self, tenant_id: str, run_id: str) -> WorkflowRunProjection:
        """按 (tenant_id, run_id) 读取；不存在/跨租户 → NotFound（E-02）。"""
        row = await self._store.get_workflow_run(tenant_id=tenant_id, run_id=run_id)
        if row is None:
            raise ConsoleResourceNotFoundError(f"workflow run not found: {run_id}")
        return _row_to_projection(row)

    async def get_run_with_history(
        self, tenant_id: str, run_id: str
    ) -> WorkflowRunDetail:
        """投影 + execution history 组合（S-11）；DBOS 侧 run 已不存在则 history=None。"""
        projection = await self.get_run(tenant_id, run_id)
        history: WorkflowExecutionHistory | None = None
        if self._workflow_engine is not None:
            try:
                history = await self._workflow_engine.get_execution_history(run_id)
            except WorkflowRunNotFoundError:
                history = None
        return WorkflowRunDetail(projection=projection, execution_history=history)

    async def list_runs(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        page: int,
        page_size: int,
    ) -> WorkflowRunPage:
        """tenant 强制 scope 的分页列表（RULE-P3-06 / B-02）。"""
        rows, total = await self._store.list_workflow_runs(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return WorkflowRunPage(
            items=tuple(_row_to_projection(row) for row in rows),
            total=total,
        )

    async def list_all_runs(
        self,
        tenant_id: str,
        *,
        page: int,
        page_size: int,
    ) -> WorkflowRunPage:
        """跨工作流 list-all（Phase 5 TASK-011，S-12：tenant scope 分页）。"""
        rows, total = await self._store.list_workflow_runs(
            tenant_id=tenant_id,
            workflow_id=None,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return WorkflowRunPage(
            items=tuple(_row_to_projection(row) for row in rows),
            total=total,
        )
