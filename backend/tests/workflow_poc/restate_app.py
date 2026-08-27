"""Restate PoC 应用层（ADR-WF-001 TASK-004）：5-step durable workflow + RestateWorkflowEngine。

Restate 是 server 模型：invocation journal 由 Restate server（container）持有，Python SDK
只暴露 handler 给 server 调用。本文件（与 DBOS PoC 同构但实现独立，候选各自评估）：

- `poc_durable_workflow`（restate.Workflow）：5-step durable workflow，用 Restate durable
  原语：`ctx.run`（持久化 step，replay 返回已记结果不重跑）/ `ctx.sleep`（durable timer）/
  `ctx.signal`（审批信号，durable promise）；
- `poc_signal`（restate.Service）：审批信号 resolver —— `ctx.signal("approve")` 的投递
  侧；外部引擎经 `/restate/lookup` 拿 invocation_id 后调 `/restate/call/poc_signal/resolve`
  投递（实测：payload 完整进入 workflow 结果）；
- `RestateWorkflowEngine`：WorkflowEngine Protocol 的 Restate 实现，纯 ingress 客户端
  （send/lookup/attach/output/cancel），不 serve app；
- `build_app()`：worker 进程 serve 的 ASGI app（uvicorn/HTTP1.1，注册用 --use-http1.1）。

幂等（S-05）：workflow 以 run_id 为 key，`/restate/send` 同 key 二次返回
`status=PreviouslyAccepted` → 引擎视为既有执行（不重跑）。
"""

from __future__ import annotations

import asyncio
import os
import time
import urllib.request
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import restate
from restate import Context, WorkflowContext

from fluxion.observability.logging import emit_workflow_event_log
from fluxion.runtime.workflow import (
    WorkflowRunStatus,
    WorkflowStartRequest,
    WorkflowStartResult,
)

RESTATE_INGRESS = os.environ.get("RESTATE_INGRESS", "http://localhost:8080")
RESTATE_ADMIN = os.environ.get("RESTATE_ADMIN", "http://localhost:9070")
WORKFLOW_NAME = "poc_durable_workflow"
SIGNAL_SERVICE = "poc_signal"
SIGNAL_NAME = "approve"
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# 业务 durable state：与 DBOS PoC 同库不同表（同一 durable 业务语义；journal 归 Restate）
DEFAULT_DBOS_DB_URL = "postgresql://mmuser:mmuser@localhost:5432/fluxion_poc_dbos"


def resolve_db_url() -> str:
    """env（FLUXION_DBOS_DATABASE_URL）> 配置文件 > 默认本地容器。"""
    from fluxion.config.workflow import WorkflowBackendSettings

    settings = WorkflowBackendSettings.resolve()
    return settings.dbos_database_url or DEFAULT_DBOS_DB_URL


# ---------------------------------------------------------------------------
# 业务表（PoC 业务侧 durable state；与 DBOS PoC 共享同库，表语义一致）
# ---------------------------------------------------------------------------

_BUSINESS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS poc_records (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (tenant_id, run_id, kind)
);
CREATE TABLE IF NOT EXISTS poc_step_executions (
    run_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    executions INT NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, step_name)
);
CREATE TABLE IF NOT EXISTS poc_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    pinned_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS poc_resource_versions (
    resource_id TEXT NOT NULL,
    version TEXT NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (resource_id, version)
);
CREATE TABLE IF NOT EXISTS poc_worker_handled (
    worker_id TEXT PRIMARY KEY,
    runs INT NOT NULL DEFAULT 0
);
"""

_BUSINESS_TABLES = (
    "poc_records",
    "poc_step_executions",
    "poc_runs",
    "poc_resource_versions",
    "poc_worker_handled",
)


def ensure_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(_BUSINESS_TABLE_DDL)


def reset_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(_BUSINESS_TABLES)}")


def _sync_business_write(statements: list[tuple[str, tuple[Any, ...]]]) -> str:
    """业务表单事务写入（ctx.run 的同步步；幂等键 ON CONFLICT 保证副作用不重复）。"""
    with psycopg.connect(resolve_db_url()) as conn:
        with conn.transaction():
            for sql, params in statements:
                conn.execute(sql, params)
    return "ok"


# ---------------------------------------------------------------------------
# trace 关联（SLO-OBS-01）：进程内 correlator + 结构化日志
# ---------------------------------------------------------------------------

_attached_correlator: Any = None


def attach_correlator(correlator: Any) -> None:
    global _attached_correlator
    _attached_correlator = correlator


def _record_event(event: str, *, run_id: str, tenant_id: str, trace_id: str) -> None:
    emit_workflow_event_log(event=event, run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    if _attached_correlator is not None:
        _attached_correlator.record(event, trace_id=trace_id, run_id=run_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# 5-step durable workflow（Restate durable 原语映射）
# ---------------------------------------------------------------------------


def _write_report_step(run_id: str, tenant_id: str, trace_id: str) -> str:
    """幂等写：以 (tenant, run, kind=report) 为幂等键；重复执行不产生第二条。

    顺带记录处理该 workflow 的 worker_id（S-06 水平扩展证据：两个真实 worker
    进程都拉到了 work）。worker_id 来自进程 env RESTATE__WORKER_ID。
    """
    worker_id = os.environ.get("RESTATE__WORKER_ID", "unknown")
    _sync_business_write(
        [
            (
                "INSERT INTO poc_step_executions (run_id, step_name, executions) "
                "VALUES (%s, 'write_report_record', 1) "
                "ON CONFLICT (run_id, step_name) DO UPDATE "
                "SET executions = poc_step_executions.executions + 1",
                (run_id,),
            ),
            (
                "INSERT INTO poc_records (tenant_id, run_id, kind, value) "
                "VALUES (%s, %s, 'report', %s) ON CONFLICT DO NOTHING",
                (tenant_id, run_id, f"report@{run_id}"),
            ),
            (
                "INSERT INTO poc_worker_handled (worker_id, runs) "
                "VALUES (%s, 1) ON CONFLICT (worker_id) DO UPDATE "
                "SET runs = poc_worker_handled.runs + 1",
                (worker_id,),
            ),
        ]
    )
    _record_event("workflow.step.report.written", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return f"report:{run_id}"


def _fetch_step(delay_seconds: float, timeout_seconds: float) -> str:
    """外部调用模拟：单步 timeout 上限（S-04 / RULE-WF-04，禁止无限等待）。

    超时抛 `TerminalError`：Restate 将之判为终态失败（不重试）→ invocation ERROR，
    而非走重试（worker 崩溃等基础设施故障才可重试，见 S-02 恢复语义）。
    """
    async def _bounded() -> str:
        async with asyncio.timeout(timeout_seconds):
            await asyncio.sleep(delay_seconds)
        return "fetched"

    try:
        return asyncio.run(_bounded())
    except TimeoutError as error:
        from restate.exceptions import TerminalError

        raise TerminalError(f"step timeout after {timeout_seconds}s") from error


def _notify_step(run_id: str, tenant_id: str, trace_id: str, url: str | None) -> str:
    """http-activity：给 url 时发真实 POST（timeout 有界），否则只记录出站活动。"""
    if url:
        def _post() -> bytes:
            with urllib.request.urlopen(url, data=b"{}", timeout=5.0) as response:  # noqa: S310
                return response.read()

        import asyncio

        asyncio.run(asyncio.to_thread(_post))
    _sync_business_write(
        [
            (
                "INSERT INTO poc_step_executions (run_id, step_name, executions) "
                "VALUES (%s, 'notify_http_endpoint', 1) "
                "ON CONFLICT (run_id, step_name) DO UPDATE "
                "SET executions = poc_step_executions.executions + 1",
                (run_id,),
            ),
            (
                "INSERT INTO poc_records (tenant_id, run_id, kind, value) "
                "VALUES (%s, %s, 'http', %s) ON CONFLICT DO NOTHING",
                (tenant_id, run_id, f"notify@{run_id}"),
            ),
        ]
    )
    _record_event("workflow.step.http.notified", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return f"notified:{run_id}"


poc_durable_workflow = restate.Workflow(WORKFLOW_NAME)


@poc_durable_workflow.main(name="run")
async def _run(ctx: WorkflowContext, request: dict) -> dict[str, Any]:
    """5-step 最小 durable workflow（与 DBOS PoC 的 POC_WORKFLOW_STEPS 逐 kind 对应）。"""
    run_id = str(request["run_id"])
    tenant_id = str(request["tenant_id"])
    trace_id = str(request["trace_id"])
    pinned_version = str(request.get("pinned_version", "v1"))

    report = await ctx.run(
        "write_report_record",
        lambda rid=run_id, tid=tenant_id, tr=trace_id: _write_report_step(rid, tid, tr),
    )
    timer_seconds = float(request.get("timer_seconds", 0.0))
    if timer_seconds > 0:
        await ctx.sleep(timedelta(seconds=timer_seconds))
    fetched = await ctx.run(
        "fetch_external_data",
        lambda d=float(request.get("external_delay_seconds", 0.0)),
        t=float(request.get("step_timeout_seconds", 30.0)): _fetch_step(d, t),
    )
    approval: Any = None
    if float(request.get("approval_timeout_seconds", 0.0)) > 0:
        approval = await ctx.signal(SIGNAL_NAME)
    notified = await ctx.run(
        "notify_http_endpoint",
        lambda rid=run_id, tid=tenant_id, tr=trace_id,
        u=request.get("http_url"): _notify_step(rid, tid, tr, u),
    )
    _record_event("workflow.steps.completed", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return {
        "run_id": run_id,
        "pinned_version": pinned_version,
        "report": report,
        "fetched": fetched,
        "approval": approval,
        "notified": notified,
    }


poc_signal = restate.Service(SIGNAL_SERVICE)


@poc_signal.handler(name="resolve")
async def _resolve_signal(ctx: Context, request: dict) -> dict[str, Any]:
    """审批信号投递：把 payload resolve 到目标 invocation 的 signal promise 上。"""
    ctx.resolve_signal(str(request["invocation_id"]), SIGNAL_NAME, request["payload"])
    return {"ok": True}


def build_app() -> Any:
    """worker 进程 serve 的 ASGI app。"""
    return restate.endpoint.app([poc_durable_workflow, poc_signal])


# ---------------------------------------------------------------------------
# RestateWorkflowEngine（WorkflowEngine Protocol 的 Restate 实现；纯 ingress 客户端）
# ---------------------------------------------------------------------------


def run_id_for(workflow_id: str, execution_id: str) -> str:
    """run_id = workflow_id:execution_id（同 execution 重放 → 同 key，dedup 生效）。"""
    return f"{workflow_id}:{execution_id}"


class RestateWorkflowEngine:
    """WorkflowEngine Protocol 的 Restate 实现（PoC，非生产）。

    - `start`：登记业务 run → `/restate/send`（key=run_id）→ 立即回查证明同步持久化；
    - `signal`：lookup invocation_id → `/restate/call/poc_signal/resolve` 投递审批信号；
    - 查询/等待：`/restate/output`（状态）/ `/restate/attach`（阻塞取结果）；
    - Restate 无原生 tenant 概念，tenant scope 由本层（poc_runs/poc_records）承载。
    """

    def __init__(self, *, ingress: str = RESTATE_INGRESS, admin: str = RESTATE_ADMIN) -> None:
        self._ingress = ingress.rstrip("/")
        self._admin = admin.rstrip("/")
        self._db_url = resolve_db_url()
        ensure_business_tables(self._db_url)

    # ---- HTTP 辅助 ----

    async def _post(self, url: str, payload: object) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            res = await client.post(url, json=payload)
        if res.status_code >= 400:
            raise RuntimeError(f"restate {url} -> {res.status_code}: {res.text[:200]}")
        return res.json()

    async def _send(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """提交 workflow（key=run_id）；AlreadyAccepted → 既有执行（幂等）。"""
        url = f"{self._ingress}/restate/send/{WORKFLOW_NAME}/{run_id}/run"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            res = await client.post(url, json=payload)
        if res.status_code >= 400:
            raise RuntimeError(f"restate send {run_id} -> {res.status_code}: {res.text[:200]}")
        return res.json()

    async def _lookup(self, run_id: str) -> str:
        data = await self._post(
            f"{self._ingress}/restate/lookup",
            {"target": "workflow", "workflowName": WORKFLOW_NAME, "workflowKey": run_id},
        )
        inv = data.get("invocationId")
        if not inv:
            raise RuntimeError(f"restate lookup {run_id} -> no invocationId: {data}")
        return str(inv)

    # ---- Protocol 成员 ----

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        arguments = request.arguments
        pinned_version = str(arguments.get("pinned_version", "v1"))
        run_id = run_id_for(request.workflow_id, str(arguments.get("execution_id", request.execution_id)))
        with psycopg.connect(self._db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO poc_runs (run_id, tenant_id, trace_id, pinned_version) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                (run_id, request.tenant_id, request.trace_id, pinned_version),
            )
        payload = {
            "run_id": run_id,
            "tenant_id": request.tenant_id,
            "trace_id": request.trace_id,
            "pinned_version": pinned_version,
            "timer_seconds": float(arguments.get("timer_seconds", 0.0)),
            "external_delay_seconds": float(arguments.get("external_delay_seconds", 0.0)),
            "step_timeout_seconds": float(arguments.get("step_timeout_seconds", 30.0)),
            "approval_timeout_seconds": float(arguments.get("approval_timeout_seconds", 0.0)),
            "http_url": arguments.get("http_url"),
        }
        await self._send(run_id, payload)
        await self._lookup(run_id)  # 立即回查：证明同步持久化（start 返回即可查）
        _record_event("workflow.started", run_id=run_id, tenant_id=request.tenant_id, trace_id=request.trace_id)
        return WorkflowStartResult(run_id=run_id, status="started")

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        """恢复语义：Restate 新进程注册后 server 自动续跑；此处返回当前投影。"""
        tenant_id, trace_id = self._run_meta(run_id)
        status = await self.get_status(run_id)
        _record_event("workflow.resume.requested", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        return status

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        tenant_id, trace_id = self._run_meta(run_id)
        invocation_id = await self._lookup(run_id)
        await self._post(
            f"{self._ingress}/restate/call/{SIGNAL_SERVICE}/resolve",
            {"invocation_id": invocation_id, "payload": payload},
        )
        _record_event("workflow.signal.sent", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        invocation_id = await self._lookup(run_id)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            res = await client.patch(f"{self._admin}/invocations/{invocation_id}/cancel")
        if res.status_code >= 400:
            raise RuntimeError(f"restate cancel {run_id} -> {res.status_code}: {res.text[:200]}")

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        status = await self._output(run_id)
        return WorkflowRunStatus(run_id=run_id, status=status)

    async def await_result(self, run_id: str, timeout: float) -> Any:
        result = await asyncio.wait_for(self._attach(run_id), timeout=timeout)
        tenant_id, trace_id = self._run_meta(run_id)
        _record_event("workflow.result.observed", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        return result

    # ---- Restate ingress 状态/结果 ----

    async def _output(self, run_id: str) -> str:
        """POST /restate/output：非阻塞查状态。

        5xx 视为瞬态（worker 崩溃后 invocation 处于恢复/重试中，broken pipe 500），
        返回 PENDING 供轮询；终态失败（TerminalError → failure 字段 / kill）→ ERROR。
        """
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            res = await client.post(
                f"{self._ingress}/restate/output",
                json={"target": "workflow", "workflowName": WORKFLOW_NAME, "workflowKey": run_id},
            )
        if res.status_code == 470:
            return "PENDING"
        if res.status_code >= 500:
            body = res.text.lower()
            # 瞬态：worker 崩溃后 invocation 恢复/重试中（broken pipe 500）→ 继续轮询
            if "broken pipe" in body or "stream closed" in body or "connection" in body:
                return "PENDING"
            return "ERROR"  # 终态失败（TerminalError 等）
        if res.status_code >= 400:
            raise RuntimeError(f"restate output {run_id} -> {res.status_code}: {res.text[:200]}")
        data = res.json()
        if data.get("failure") is not None:
            return "ERROR"
        if data.get("message") == "not ready":
            return "PENDING"
        return "SUCCESS"

    async def _attach(self, run_id: str) -> Any:
        """POST /restate/attach：阻塞等完成，返回 workflow 结果。"""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            res = await client.post(
                f"{self._ingress}/restate/attach",
                json={"target": "workflow", "workflowName": WORKFLOW_NAME, "workflowKey": run_id},
            )
        if res.status_code >= 400:
            raise RuntimeError(f"restate attach {run_id} -> {res.status_code}: {res.text[:200]}")
        data = res.json()
        if data.get("failure") is not None:
            raise RuntimeError(f"workflow {run_id} failed: {data['failure']}")
        return data

    # ---- PoC 查询/种子 helper（业务表；tenant scope 在此承载） ----

    def list_run_ids(self, tenant_id: str) -> list[str]:
        return self._fetch_column(
            "SELECT run_id FROM poc_runs WHERE tenant_id = %s ORDER BY run_id", (tenant_id,)
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._db_url) as conn:
            row = conn.execute(
                "SELECT run_id, tenant_id, trace_id, pinned_version FROM poc_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "tenant_id": row[1],
            "trace_id": row[2],
            "pinned_version": row[3],
        }

    def list_records(self, tenant_id: str) -> list[tuple[str, str]]:
        with psycopg.connect(self._db_url) as conn:
            rows = conn.execute(
                "SELECT run_id, kind FROM poc_records WHERE tenant_id = %s ORDER BY run_id, kind",
                (tenant_id,),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def step_executions(self, run_id: str) -> dict[str, int]:
        with psycopg.connect(self._db_url) as conn:
            rows = conn.execute(
                "SELECT step_name, executions FROM poc_step_executions WHERE run_id = %s",
                (run_id,),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def handled_workers(self) -> dict[str, int]:
        """哪些 worker 处理过 workflow（S-06 水平扩展证据）。"""
        with psycopg.connect(self._db_url) as conn:
            rows = conn.execute(
                "SELECT worker_id, runs FROM poc_worker_handled ORDER BY worker_id"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def set_resource_version(self, resource_id: str, version: str, *, is_latest: bool) -> None:
        with psycopg.connect(self._db_url, autocommit=True) as conn:
            if is_latest:
                conn.execute(
                    "UPDATE poc_resource_versions SET is_latest = false WHERE resource_id = %s",
                    (resource_id,),
                )
            conn.execute(
                "INSERT INTO poc_resource_versions (resource_id, version, is_latest) "
                "VALUES (%s, %s, %s) ON CONFLICT (resource_id, version) "
                "DO UPDATE SET is_latest = EXCLUDED.is_latest",
                (resource_id, version, is_latest),
            )

    def _run_meta(self, run_id: str) -> tuple[str, str]:
        run = self.get_run(run_id)
        if run is None:
            return "unknown-tenant", f"trace-{run_id}"
        return run["tenant_id"], run["trace_id"]

    def _fetch_column(self, sql: str, params: tuple[Any, ...]) -> list[str]:
        with psycopg.connect(self._db_url) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row[0] for row in rows]
