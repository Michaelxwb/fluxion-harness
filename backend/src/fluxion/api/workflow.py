"""Workflow status projection API（TASK-008 / FEAT-P3-06，design §3.4）。

Console-facing 只读投影：`GET /api/v1/workflows/runs/{run_id}`（单 run：node 级
状态 + pinned refs + execution history）、`GET /api/v1/workflows/{workflow_id}/runs`
（分页列表）。统一 envelope（`success()`，RULE-fluxion-console-api-001，Handler
不手写响应结构）；404 由 `ConsoleResourceNotFoundError` 经异常中间件映射。
Runtime 边界不内侵（RULE-fluxion-console-001）：execution history 读取下沉到
services 层（`WorkflowProjectionService.get_run_with_history`），本层只依赖
`fluxion.services`，不 import `fluxion.runtime.*`（架构守护）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from fluxion.api.console_helpers import _actor, _page
from fluxion.api.responses import success
from fluxion.services.workflow_projection import (
    WorkflowProjectionService,
    WorkflowRunDetail,
    WorkflowRunProjection,
)


def register_workflow_projection_routes(
    app: FastAPI,
    *,
    projection_service: WorkflowProjectionService,
) -> None:
    """注册投影只读路由；execution history 由 service 注入 engine 时返回。"""

    @app.get("/api/v1/workflows/runs/{run_id}")
    async def get_workflow_run(run_id: str) -> JSONResponse:
        actor = _actor(None)
        detail = await projection_service.get_run_with_history(actor.tenant_id, run_id)
        return success(_run_detail_payload(detail))

    @app.get("/api/v1/workflows/{workflow_id}/runs")
    async def list_workflow_runs(
        workflow_id: str,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        actor = _actor(None)
        result = await projection_service.list_runs(
            actor.tenant_id, workflow_id, page=page, page_size=page_size
        )
        items = [_run_payload(item) for item in result.items]
        return success(_page(items, page, page_size, result.total))


# DBOS 状态（大写）→ 投影状态（小写，design §3.3 status 枚举）——API 面向
# Console/Workflow Studio，统一一套状态词汇，避免 SUCCESS/succeeded 双轨。
_HISTORY_STATUS_MAP = {
    "SUCCESS": "succeeded",
    "ERROR": "failed",
    "CANCELLED": "cancelled",
    "PENDING": "running",
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "failed",
}


def _run_detail_payload(detail: WorkflowRunDetail) -> dict[str, object]:
    return _run_payload(detail.projection, detail.execution_history)


def _run_payload(
    projection: WorkflowRunProjection,
    history: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_id": projection.run_id,
        "status": projection.status,
        "workflow_id": projection.workflow_id,
        "workflow_version": projection.workflow_version,
        "execution_id": projection.execution_id,
        "trace_id": projection.trace_id,
        "pinned_refs": projection.pinned_refs,
        "node_states": projection.node_states,
        "created_at": _iso(projection.created_at),
        "updated_at": _iso(projection.updated_at),
    }
    if history is not None:
        payload["execution_history"] = {
            "run_id": getattr(history, "run_id", ""),
            "status": _HISTORY_STATUS_MAP.get(
                getattr(history, "status", ""), getattr(history, "status", "").lower()
            ),
            "steps": [
                {
                    "node_id": getattr(step, "node_id", ""),
                    "status": _HISTORY_STATUS_MAP.get(
                        getattr(step, "status", ""), getattr(step, "status", "").lower()
                    ),
                    "output": getattr(step, "output", None),
                    "error": getattr(step, "error", None),
                }
                for step in getattr(history, "steps", ())
            ],
        }
    return payload


def _iso(value: object) -> str | None:
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else None
