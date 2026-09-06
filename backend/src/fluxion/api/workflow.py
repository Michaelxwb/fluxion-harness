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
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.workflow_app import WorkflowDefinitionValidator
from fluxion.services.workflow_projection import (
    WorkflowProjectionService,
    WorkflowRunDetail,
    WorkflowRunProjection,
)


def register_workflow_projection_routes(
    app: FastAPI,
    *,
    projection_service: WorkflowProjectionService,
    service: ConsoleApplicationService | None = None,
) -> None:
    """注册投影只读路由；execution history 由 service 注入 engine 时返回。

    TASK-015：service 注入时一并注册 V2 schema / validate 端点（此前仅
    in-memory API 实现，HTTP 404——F-S-09 E2E 抓到的真实缺口）。
    """
    if service is not None:
        _register_workflow_schema_routes(app, service)

    @app.get("/api/v1/workflows/runs/{run_id}")
    async def get_workflow_run(run_id: str) -> JSONResponse:
        actor = _actor(None)
        detail = await projection_service.get_run_with_history(actor.tenant_id, run_id)
        return success(_run_detail_payload(detail))

    @app.get("/api/v1/workflows/runs")
    async def list_all_workflow_runs(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        """跨工作流 runs list-all（Phase 5 TASK-011，S-12：tenant scope 分页）。"""
        actor = _actor(None)
        result = await projection_service.list_all_runs(
            actor.tenant_id, page=page, page_size=page_size
        )
        items = [_run_payload(item) for item in result.items]
        return success(_page(items, page, page_size, result.total))

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
        # review P1-5：wire 契约为 string（前端 requiredString；in-memory fixture
        # 也是 "v1" 字符串）——DB 内部 int，API 层序列化收口，修复真实投影数据
        # 下 RunsPage 整卡解析失败。
        "workflow_version": str(projection.workflow_version),
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


# ---- TASK-015：Workflow V2 schema / validate（Designer 依赖端点） ----

# V2 九节点 schema 冻结契约（design §2.3.2）；与前端 workflowV2.ts 的
# WORKFLOW_V2_SCHEMA 同构（node_kinds[].fields[].{field,required,title,type}）。
# 节点判别模型见 resources/workflow_nodes.py——schema 表新增 kind 时必须同步。
_WORKFLOW_NODE_KIND_FIELDS: dict[str, list[dict[str, object]]] = {
    "capability": [
        {"field": "capability_ref", "required": True, "title": "能力引用", "type": "string", "description": "(skill|tool|mcp|plugin):<id>@<version>"},
        {"field": "input", "required": False, "title": "静态输入", "type": "object"},
    ],
    "agent": [
        {"field": "agent_ref", "required": True, "title": "Agent 引用", "type": "string", "description": "agent:<id>@<version>"},
        {"field": "prompt", "required": False, "title": "提示词", "type": "string"},
        {"field": "max_turns", "required": False, "title": "回合上限", "type": "number"},
        {"field": "input", "required": False, "title": "静态输入", "type": "object"},
    ],
    "condition": [
        {"field": "expression", "required": True, "title": "谓词表达式", "type": "string", "description": "白名单表达式，支持 {{ node_id.output }} 插值"},
        {"field": "then", "required": True, "title": "真分支后继", "type": "array"},
        {"field": "else", "required": False, "title": "假分支后继", "type": "array"},
    ],
    "switch": [
        {"field": "expression", "required": True, "title": "路由表达式", "type": "string"},
        {"field": "cases", "required": True, "title": "分支", "type": "array", "description": "至少 1 项：{ value, node_ids }"},
        {"field": "default", "required": False, "title": "默认后继", "type": "array"},
    ],
    "parallel": [
        {"field": "branches", "required": True, "title": "并行分支", "type": "array", "description": "至少 2 项：{ branch_id, node_ids }"},
        {"field": "join_policy", "required": False, "title": "汇聚策略", "type": "string", "description": "all | any"},
    ],
    "transform": [
        {"field": "source", "required": True, "title": "来源引用", "type": "string"},
        {"field": "transform", "required": True, "title": "变换模板", "type": "string"},
    ],
    "wait": [
        {"field": "duration_seconds", "required": True, "title": "等待秒数", "type": "number", "description": "> 0"},
    ],
    "human_task": [
        {"field": "assignee", "required": True, "title": "审批人", "type": "string", "description": "user ref / role"},
        {"field": "message", "required": False, "title": "审批提示", "type": "string"},
        {"field": "timeout_seconds", "required": False, "title": "审批超时（秒）", "type": "number"},
    ],
    "subworkflow": [
        {"field": "workflow_ref", "required": True, "title": "子流程引用", "type": "string", "description": "workflow:<id>@<version>"},
        {"field": "input", "required": False, "title": "静态输入", "type": "object"},
    ],
}

_WORKFLOW_NODE_KIND_TITLES: dict[str, str] = {
    "capability": "能力节点",
    "agent": "智能体节点",
    "condition": "条件路由",
    "switch": "多路路由",
    "parallel": "并行分支",
    "transform": "值变换",
    "wait": "定时等待",
    "human_task": "人工审批",
    "subworkflow": "子流程",
}

_WORKFLOW_COMMON_FIELDS: list[dict[str, object]] = [
    {"field": "id", "required": True, "title": "节点 ID", "type": "string"},
    {"field": "depends_on", "required": False, "title": "前置节点", "type": "array"},
    {"field": "timeout_ms", "required": False, "title": "节点超时（毫秒）", "type": "number"},
    {"field": "retry_policy", "required": False, "title": "重试意愿", "type": "object"},
]


def _register_workflow_schema_routes(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.get("/api/v1/workflows/schema")
    async def get_workflow_schema() -> JSONResponse:
        node_kinds = [
            {
                "fields": [
                    *_WORKFLOW_COMMON_FIELDS,
                    *_WORKFLOW_NODE_KIND_FIELDS[kind],
                ],
                "kind": kind,
                "title": title,
            }
            for kind, title in _WORKFLOW_NODE_KIND_TITLES.items()
        ]
        return success({"node_kinds": node_kinds})

    @app.post("/api/v1/workflows/validate")
    async def validate_workflow_draft(
        payload: dict[str, object],
    ) -> JSONResponse:
        """V2 草稿校验（后端权威）：pydantic 判别联合 + capability refs 可用性。

        诊断结构 {node_id?, field, message}——Designer 逐字段定位（E-02）。
        """
        actor = _actor(None)
        validator = WorkflowDefinitionValidator(service.store)
        result = await validator.validate_structured(
            tenant_id=actor.tenant_id,
            spec=payload,
        )
        return success(
            {
                "valid": result.valid,
                "diagnostics": [
                    {
                        "field": item.field,
                        "message": item.message,
                        "node_id": item.node_id,
                    }
                    for item in result.diagnostics
                ],
            }
        )
