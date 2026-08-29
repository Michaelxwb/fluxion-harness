"""Operations 运营视图（Phase 5 TASK-010 / FEAT-P5-07，S-11）。

DBOS sysdb **只读**（Fluxion 不直写）：
- `GET /api/v1/operations/queues`：`dbos.queues` + `dbos.workflow_status` 排队计数
  （depth = status='ENQUEUED' 行数；workers = worker_concurrency 配置）；
- `GET /api/v1/operations/workers`：`dbos.workflow_status.executor_id` 派生 worker
  实例视图（queues/running 计数/started_at/状态）。

边界说明（tenant scope）：DBOS sysdb 无租户列（workflow 队列为部署级平台基础设施）；
tenant 经请求上下文（envelope + X-Tenant-ID）记录，数据为部署级只读。查询带
deadline（规则 18），sysdb 不可达 → OperationsUnavailableError（明确失败不静默）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_IDLE_THRESHOLD_MS = 15 * 60 * 1000  # 最近 15 分钟有活动 → idle；更早 → stopped
_QUERY_TIMEOUT_SECONDS = 3.0


class OperationsUnavailableError(RuntimeError):
    """sysdb 不可达或查询失败（明确失败，不静默空数据）。"""

    code = "operations_unavailable"


def _to_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


class OperationsApplicationService:
    """DBOS sysdb 只读运营视图（未装配 sysdb 时返回空——dev 无 DBOS）。"""

    def __init__(self, sysdb_dsn: str | None = None) -> None:
        self._engine: AsyncEngine | None = (
            create_async_engine(_to_async_dsn(sysdb_dsn)) if sysdb_dsn else None
        )

    @property
    def configured(self) -> bool:
        return self._engine is not None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def list_queues(self) -> list[dict[str, object]]:
        """queue 列表：dbos.queues 注册行 + ENQUEUED 计数（depth）。

        合并「已注册」与「有排队行但未注册」（enqueue 不要求 register——PoC 实测
        dbos.queues 无 fluxion-workflow 行时 workflow_status 仍可 ENQUEUED）。
        """
        if self._engine is None:
            return []
        registered = await self._query(
            text("SELECT queue_id, name, worker_concurrency FROM dbos.queues ORDER BY name")
        )
        depths = await self._query(
            text(
                """
                SELECT queue_name, COUNT(*) AS depth
                FROM dbos.workflow_status
                WHERE status = 'ENQUEUED' AND queue_name IS NOT NULL
                GROUP BY queue_name
                """
            )
        )
        depth_by_name = {str(row.queue_name): int(row.depth) for row in depths}
        queues: dict[str, dict[str, object]] = {}
        for row in registered:
            name = str(row.name)
            queues[name] = {
                "queue_id": str(row.queue_id),
                "name": name,
                "depth": depth_by_name.get(name, 0),
                "workers": int(row.worker_concurrency or 0),
            }
        for name, depth in depth_by_name.items():
            if name not in queues:
                queues[name] = {"queue_id": "", "name": name, "depth": depth, "workers": 0}
        return sorted(queues.values(), key=lambda item: str(item["name"]))

    async def list_workers(self) -> list[dict[str, object]]:
        """worker 实例视图：executor_id 派生（queues/running/started_at/状态）。"""
        if self._engine is None:
            return []
        rows = await self._query(
            text(
                """
                SELECT executor_id,
                       COUNT(*) FILTER (WHERE status = 'RUNNING') AS running,
                       COUNT(*) FILTER (WHERE status IN ('RUNNING', 'ENQUEUED', 'PENDING')) AS active,
                       MIN(created_at) AS first_seen_ms,
                       MAX(updated_at) AS last_active_ms,
                       ARRAY_AGG(DISTINCT queue_name) FILTER (WHERE queue_name IS NOT NULL) AS queues
                FROM dbos.workflow_status
                WHERE executor_id IS NOT NULL AND executor_id != ''
                GROUP BY executor_id
                ORDER BY first_seen_ms
                """
            )
        )
        now_ms = datetime.now(UTC).timestamp() * 1000
        workers: list[dict[str, object]] = []
        for row in rows:
            last_active = int(row.last_active_ms or 0)
            running = int(row.running or 0)
            if running > 0:
                status = "running"
            elif now_ms - last_active <= _IDLE_THRESHOLD_MS:
                status = "idle"
            else:
                status = "stopped"
            workers.append(
                {
                    "worker_id": str(row.executor_id),
                    "status": status,
                    "queues": sorted(str(q) for q in (row.queues or [])),
                    "started_at": _ms_to_iso(row.first_seen_ms),
                    "running_workflows": int(row.active or 0),
                }
            )
        return workers

    async def _query(self, statement: Any) -> list[Any]:
        assert self._engine is not None
        try:
            async with self._engine.connect() as conn:
                result = await asyncio.wait_for(
                    conn.execute(statement), timeout=_QUERY_TIMEOUT_SECONDS
                )
                return list(result.fetchall())
        except TimeoutError as error:
            raise OperationsUnavailableError(
                f"sysdb 查询超时（>{_QUERY_TIMEOUT_SECONDS}s）"
            ) from error
        except SQLAlchemyError as error:
            raise OperationsUnavailableError(f"sysdb 查询失败: {error}") from error


def _ms_to_iso(epoch_ms: int | None) -> str:
    if epoch_ms is None:
        return ""
    return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=UTC).isoformat()
