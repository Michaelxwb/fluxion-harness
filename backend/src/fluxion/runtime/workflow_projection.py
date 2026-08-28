"""`workflow_run` 投影的 worker 侧 writer（TASK-008 / FEAT-P3-06）。

写路径：DBOS worker 进程（解释器 + `fluxion-workflow-worker` CLI）经本 writer
同步 psycopg 写入 Fluxion 域 `workflow_run` 表（与 DBOS sysdb 同库不同表）。
DDL 与 `registry/schema.py` 的 metadata 保持一致，`ensure_workflow_run_table`
幂等（CREATE TABLE IF NOT EXISTS + 索引），满足 RULE-backend-database-001。

读路径在 API/Console（`services/workflow_projection.py` + registry async CRUD），
同一张表跨驱动读写（psycopg worker ↔ asyncpg store），契约测试保证 schema 对齐。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

import psycopg
from psycopg.types.json import Json

# 与 `registry/schema.py` metadata 对齐（P1-12）：列宽、复合 PK (tenant_id, run_id)
# 与 async store 侧一致（rule 16，跨租户同 run_id 不串写）。两处事实源必须同步修改。
WORKFLOW_RUN_DDL = """
CREATE TABLE IF NOT EXISTS workflow_run (
    tenant_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(512) NOT NULL,
    workflow_id VARCHAR(128) NOT NULL,
    workflow_version INTEGER NOT NULL,
    execution_id VARCHAR(128) NOT NULL,
    trace_id VARCHAR(128) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    node_states JSON,
    pinned_refs JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, run_id)
)
"""

WORKFLOW_RUN_INDEX_TENANT = "CREATE INDEX IF NOT EXISTS idx_wf_run_tenant ON workflow_run (tenant_id)"
WORKFLOW_RUN_INDEX_EXEC = "CREATE INDEX IF NOT EXISTS idx_wf_run_exec ON workflow_run (execution_id)"


def ensure_workflow_run_table(database_url: str) -> None:
    """幂等建表 + 索引（CREATE IF NOT EXISTS，worker/bootstrap 装配时调用）。"""
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(WORKFLOW_RUN_DDL)
        connection.execute(WORKFLOW_RUN_INDEX_TENANT)
        connection.execute(WORKFLOW_RUN_INDEX_EXEC)


def release_workflow_active_references(
    database_url: str,
    *,
    tenant_id: str,
    ref_type: str,
    ref_id: str,
) -> None:
    """worker 侧 sync 释放 active refs（与 `release_active_references_for_ref` 同 SQL）。

    DBOS workflow 函数在独立 event loop 上运行，不能调 async SQLAlchemy engine
    （"Future attached to a different loop"）；与投影 writer 一样走 sync psycopg。
    由 `set_reference_releaser` 注入 worker 进程，解释器/CLI 终态统一调用（P0-2）。
    """
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            "DELETE FROM active_references "
            "WHERE tenant_id = %s AND ref_type = %s AND ref_id = %s",
            (tenant_id, ref_type, ref_id),
        )


class WorkflowRunProjectionWriter:
    """同步 psycopg writer（DBOS worker 进程内调用，连接按次建立、自动提交）。

    全部 SQL 参数化（RULE-backend-database-001）；node_states 整批单行写入
    （PATTERN-backend-003，无循环内单条 UPDATE / N+1）；upsert 幂等（replay 安全）。
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._lock = threading.Lock()

    def upsert_run(self, run_meta: Mapping[str, object]) -> None:
        """run 启动时创建/复位投影行（status=running，pinned_refs 版本快照）。"""
        run_id = str(run_meta.get("run_id", ""))
        tenant_id = str(run_meta.get("tenant_id", ""))
        execution_id = str(run_meta.get("execution_id", ""))
        trace_id = str(run_meta.get("trace_id", ""))
        pinned = list(run_meta.get("pinned", []) or [])
        workflow_ref = next(
            (r for r in pinned if isinstance(r, Mapping) and r.get("kind") == "workflow"),
            None,
        )
        workflow_id = str(workflow_ref.get("id", "")) if workflow_ref else ""
        version = str(workflow_ref.get("version", "")) if workflow_ref else ""
        workflow_version = int(version) if version.isdigit() else 0
        self._execute(
            "INSERT INTO workflow_run "
            "(run_id, tenant_id, workflow_id, workflow_version, execution_id, trace_id, "
            " status, pinned_refs, node_states, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s, NOW(), NOW()) "
            "ON CONFLICT (tenant_id, run_id) DO UPDATE SET "
            " status = 'running', node_states = %s, updated_at = NOW()",
            (
                run_id,
                tenant_id,
                workflow_id,
                workflow_version,
                execution_id,
                trace_id,
                Json(pinned),
                Json({}),
                Json({}),
            ),
        )

    def update_node_states(
        self,
        *,
        tenant_id: str,
        run_id: str,
        node_states: Mapping[str, object],
    ) -> None:
        """分批写 node_states（单行 UPDATE，一批内多节点一次落库）。"""
        self._execute(
            "UPDATE workflow_run SET node_states = %s, updated_at = NOW() "
            "WHERE run_id = %s AND tenant_id = %s",
            (Json(dict(node_states)), run_id, tenant_id),
        )

    def finish_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        status: str,
        node_states: Mapping[str, object] | None = None,
    ) -> None:
        """terminal 状态更新（succeeded/failed/cancelled）；node_states 可选覆盖。"""
        if node_states is None:
            self._execute(
                "UPDATE workflow_run SET status = %s, updated_at = NOW() "
                "WHERE run_id = %s AND tenant_id = %s",
                (status, run_id, tenant_id),
            )
            return
        self._execute(
            "UPDATE workflow_run SET status = %s, node_states = %s, updated_at = NOW() "
            "WHERE run_id = %s AND tenant_id = %s",
            (status, Json(dict(node_states)), run_id, tenant_id),
        )

    def _execute(self, sql: str, params: tuple[object, ...]) -> None:
        with self._lock:
            with psycopg.connect(self._database_url, autocommit=True) as connection:
                connection.execute(sql, params)


# ---------------------------------------------------------------------------
# 进程级 writer 注入（镜像 definition provider / reference store 装配模式）
# ---------------------------------------------------------------------------

_projection_writer_instance: WorkflowRunProjectionWriter | None = None
_projection_writer_lock = threading.Lock()


def set_projection_writer(writer: WorkflowRunProjectionWriter | None) -> None:
    global _projection_writer_instance
    with _projection_writer_lock:
        _projection_writer_instance = writer


def get_projection_writer() -> WorkflowRunProjectionWriter | None:
    with _projection_writer_lock:
        return _projection_writer_instance
