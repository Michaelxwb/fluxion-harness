"""DBOS PoC 应用层（ADR-WF-001 TASK-003）：5-step durable workflow + DBOSWorkflowEngine。

vendor pick 未决（pending-PoC-gate，TASK-005 矩阵回填后才定），实现置于 tests/
而非 src/；DBOS 中选再上移生产化。

关键机制（smoke 验证，DBOS 2.31）：
- `DBOS.start_workflow` 非阻塞、loop 内可直接调用；返回即同步持久化；
- 客户端查询/信号 API 统一 `asyncio.to_thread(同步 API)`：DBOS `*_async` 客户端
  方法绑定首个 event loop，pytest 每测试新 loop 会报
  "cannot schedule new futures after shutdown"（已实测，记入 evidence）；
- workflow 内部 `sleep_async`/`recv_async` 跑在 DBOS 自管 loop，不受影响；
- `DBOS.launch()` 内置 startup recovery（按 app version 恢复 PENDING workflows）。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import psycopg
from dbos import DBOS, Queue, SetWorkflowID
from dbos._dbos_config import DBOSConfig

from fluxion.config.workflow import WorkflowBackendSettings
from fluxion.errors.workflow import (
    WorkflowBackendUnavailableError,
    WorkflowRunNotFoundError,
)
from fluxion.observability.logging import emit_workflow_event_log
from fluxion.runtime.workflow import (
    WorkflowRunStatus,
    WorkflowStartRequest,
    WorkflowStartResult,
)

DBOS_APP_NAME = "fluxion-poc-dbos"
DEFAULT_DBOS_DB_URL = "postgresql://mmuser:mmuser@localhost:5432/fluxion_poc_dbos"
QUEUE_NAME = "poc_queue"
# PoC 并发上限：模拟连接池语义，防止 1000 并发打爆 postgres max_connections
_DB_CONCURRENCY = asyncio.Semaphore(20)


def resolve_db_url() -> str:
    """env（FLUXION_DBOS_DATABASE_URL）> 配置文件 > 默认本地容器。"""
    settings = WorkflowBackendSettings.resolve()
    return settings.dbos_database_url or DEFAULT_DBOS_DB_URL


# ---------------------------------------------------------------------------
# 业务表（PoC 业务侧 durable state；与 DBOS sysdb 同库不同表）
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
"""

_BUSINESS_TABLES = ("poc_records", "poc_step_executions", "poc_runs", "poc_resource_versions")


def ensure_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(_BUSINESS_TABLE_DDL)


def reset_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(_BUSINESS_TABLES)}")


def purge_stale_enqueued(db_url: str) -> None:
    """清理 poc_queue 遗留 ENQUEUED 行（上次中断运行的残留），避免被 worker 误认领。"""
    sysdb_url = f"{db_url.rstrip('/')}_dbos_sys"
    with psycopg.connect(sysdb_url, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM dbos.workflow_status WHERE queue_name = %s AND status = 'ENQUEUED'",
            (QUEUE_NAME,),
        )


# ---------------------------------------------------------------------------
# trace 关联（SLO-OBS-01）：进程内 correlator + 结构化日志
# ---------------------------------------------------------------------------

_attached_correlator: Any = None


def attach_correlator(correlator: Any) -> None:
    global _attached_correlator
    _attached_correlator = correlator


def _record_event(event: str, *, run_id: str, tenant_id: str, trace_id: str) -> None:
    emit_workflow_event_log(
        event=event, run_id=run_id, tenant_id=tenant_id, trace_id=trace_id
    )
    if _attached_correlator is not None:
        _attached_correlator.record(
            event, trace_id=trace_id, run_id=run_id, tenant_id=tenant_id
        )


# ---------------------------------------------------------------------------
# DBOS 实例（模块导入即 config；launch 幂等）
# ---------------------------------------------------------------------------

DBOS(
    config=DBOSConfig(
        name=DBOS_APP_NAME, database_url=resolve_db_url(), run_admin_server=False
    )
)
# database_backed_queue=True 的 Queue 不会注册进内存 registry（_queue.py
# 明确 "source of truth is the queues table"），必须在 launch 后用
# DBOS.register_queue() 把配置持久化到 dbos.queues，queue_thread（list_queues
# 每 1s 刷新）才会轮询消费。仅构造 Queue() 会 "Listening to 0 queues"，
# enqueue 后无人消费、get_result 永久阻塞（ADR-WF-001 S-06 实测根因）。
# register_queue 要求非 async 上下文（check_async 硬 raise），而本工程在
# async 测试/worker 进程内直接调用 launch_dbos()，故从后台线程注册
# （线程内无 event loop），注册后 ≤1s 被 queue_thread 接管。
# worker_concurrency=4：无该上限时 start_queued_workflows 的 max_tasks 为
# sys.maxsize，首个 poll 的进程会一次性认领全部任务，第 2 个 worker 拉不到
# work（S-06 实测：全部 executor_id 为 'local'）；4 使每个 worker 各认领 ≤4，
# 8 个任务必然 4/4 分摊。
_WORKER_CONCURRENCY = 4
POC_QUEUE = Queue(
    QUEUE_NAME, database_backed_queue=True, worker_concurrency=_WORKER_CONCURRENCY
)
_launched = False


def launch_dbos(*, listen: list[str] | None = None) -> Queue:
    """启动 DBOS（含 startup recovery）并确保 poc_queue 被监听（幂等）。

    listen 仅在未 launch 时生效：测试驱动进程传 []（不监听用户队列，S-06
    只允许 2 个 worker 消费）；worker 进程用默认 None（监听全部 DB 队列）。
    """
    global _launched
    if not _launched:
        from dbos._dbos import _dbos_global_instance

        globally_launched = (
            _dbos_global_instance is not None
            and getattr(_dbos_global_instance, "_launched", False) is True
        )
        if not globally_launched:
            if listen is not None:
                DBOS.listen_queues(listen)  # 必须在 DBOS.launch() 之前调用
            DBOS.launch()
        threading.Thread(
            target=DBOS.register_queue,
            kwargs={
                "name": QUEUE_NAME,
                "polling_interval_sec": 1.0,
                "worker_concurrency": _WORKER_CONCURRENCY,
            },
            daemon=True,
        ).start()
        _launched = True
    return POC_QUEUE


# ---------------------------------------------------------------------------
# steps / workflows
# ---------------------------------------------------------------------------


async def _business_write(statements: list[tuple[str, tuple[Any, ...]]]) -> None:
    """业务表写入（单事务；信号量限流模拟连接池）。"""
    async with (
        _DB_CONCURRENCY,
        await psycopg.AsyncConnection.connect(resolve_db_url()) as conn,
        conn.transaction(),
    ):
        for sql, params in statements:
            await conn.execute(sql, params)


@DBOS.step()
async def write_report_record(run_id: str, tenant_id: str, trace_id: str) -> str:
    """幂等写：以 (tenant, run, kind=report) 为幂等键；重复执行不产生第二条。"""
    await _business_write(
        [
            (
                (
                    "INSERT INTO poc_step_executions (run_id, step_name, executions) "
                    "VALUES (%s, 'write_report_record', 1) "
                    "ON CONFLICT (run_id, step_name) DO UPDATE "
                    "SET executions = poc_step_executions.executions + 1"
                ),
                (run_id,),
            ),
            (
                (
                    "INSERT INTO poc_records (tenant_id, run_id, kind, value) "
                    "VALUES (%s, %s, 'report', %s) ON CONFLICT DO NOTHING"
                ),
                (tenant_id, run_id, f"report@{run_id}"),
            ),
        ]
    )
    _record_event("workflow.step.report.written", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return f"report:{run_id}"


@DBOS.step()
async def fetch_external_data(
    run_id: str, tenant_id: str, trace_id: str, delay_seconds: float, timeout_seconds: float
) -> str:
    """外部调用模拟：单步 timeout 上限（S-04 / RULE-WF-04，禁止无限等待）。"""
    _record_event("workflow.step.external.started", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    async with asyncio.timeout(timeout_seconds):
        await asyncio.sleep(delay_seconds)
    _record_event("workflow.step.external.completed", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return f"fetched:{run_id}"


@DBOS.step()
async def notify_http_endpoint(
    run_id: str, tenant_id: str, trace_id: str, url: str | None
) -> str:
    """http-activity：默认持久记录出站活动；给 url 时发真实 POST（timeout 有界）。"""
    if url:
        import urllib.request

        def _post() -> bytes:
            with urllib.request.urlopen(url, data=b"{}", timeout=5.0) as response:
                return response.read()

        await asyncio.to_thread(_post)
    await _business_write(
        [
            (
                (
                    "INSERT INTO poc_step_executions (run_id, step_name, executions) "
                    "VALUES (%s, 'notify_http_endpoint', 1) "
                    "ON CONFLICT (run_id, step_name) DO UPDATE "
                    "SET executions = poc_step_executions.executions + 1"
                ),
                (run_id,),
            ),
            (
                (
                    "INSERT INTO poc_records (tenant_id, run_id, kind, value) "
                    "VALUES (%s, %s, 'http', %s) ON CONFLICT DO NOTHING"
                ),
                (tenant_id, run_id, f"notify@{run_id}"),
            ),
        ]
    )
    _record_event("workflow.step.http.notified", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return f"notified:{run_id}"


def approval_topic(run_id: str) -> str:
    return f"approve:{run_id}"


@DBOS.workflow()
async def poc_durable_workflow(
    run_id: str,
    tenant_id: str,
    trace_id: str,
    pinned_version: str,
    *,
    timer_seconds: float = 0.0,
    external_delay_seconds: float = 0.0,
    step_timeout_seconds: float = 30.0,
    approval_timeout_seconds: float = 0.0,
    http_url: str | None = None,
) -> dict[str, Any]:
    """5-step 最小 durable workflow（POC_WORKFLOW_STEPS 逐 kind 对应）。"""
    report = await write_report_record(run_id, tenant_id, trace_id)
    if timer_seconds > 0:
        await DBOS.sleep_async(timer_seconds)  # durable timer
    fetched = await fetch_external_data(
        run_id, tenant_id, trace_id, external_delay_seconds, step_timeout_seconds
    )
    approval: Any = None
    if approval_timeout_seconds > 0:
        approval = await DBOS.recv_async(
            topic=approval_topic(run_id), timeout_seconds=approval_timeout_seconds
        )
    notified = await notify_http_endpoint(run_id, tenant_id, trace_id, http_url)
    _record_event("workflow.steps.completed", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
    return {
        "run_id": run_id,
        "pinned_version": pinned_version,
        "report": report,
        "fetched": fetched,
        "approval": approval,
        "notified": notified,
    }


@DBOS.workflow()
async def poc_baseline_workflow(run_id: str, tenant_id: str, trace_id: str) -> str:
    """1000-concurrent baseline 最小 workflow（单幂等写 step）。"""
    await write_report_record(run_id, tenant_id, trace_id)
    return run_id


# ---------------------------------------------------------------------------
# DBOSWorkflowEngine（WorkflowEngine Protocol 的 DBOS 候选实现）
# ---------------------------------------------------------------------------


def run_id_for(workflow_id: str, execution_id: str) -> str:
    """run_id = workflow_id:execution_id（同 execution 重放 → 同 run，dedup 生效）。"""
    return f"{workflow_id}:{execution_id}"


class DBOSWorkflowEngine:
    """WorkflowEngine Protocol 的 DBOS 实现（PoC，非生产）。

    - `start`：登记业务 run → `SetWorkflowID` + 非阻塞 start → 立即回查证明同步持久化；
    - 查询/信号类 DBOS API 统一 `to_thread`（跨 event loop 安全）；
    - DBOS 无原生 tenant 概念，tenant scope 由本层（poc_runs/poc_records）承载。
    """

    def __init__(self, *, auto_launch: bool = True, listen: list[str] | None = None) -> None:
        self._db_url = resolve_db_url()
        ensure_business_tables(self._db_url)
        if auto_launch:
            launch_dbos(listen=listen)

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        arguments = request.arguments
        pinned_version = str(arguments.get("pinned_version", "v1"))
        run_id = run_id_for(
            request.workflow_id, str(arguments.get("execution_id", request.execution_id))
        )
        with psycopg.connect(self._db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO poc_runs (run_id, tenant_id, trace_id, pinned_version) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                (run_id, request.tenant_id, request.trace_id, pinned_version),
            )
        with SetWorkflowID(run_id):
            DBOS.start_workflow(
                poc_durable_workflow,
                run_id,
                request.tenant_id,
                request.trace_id,
                pinned_version,
                timer_seconds=float(arguments.get("timer_seconds", 0.0)),
                external_delay_seconds=float(arguments.get("external_delay_seconds", 0.0)),
                step_timeout_seconds=float(arguments.get("step_timeout_seconds", 30.0)),
                approval_timeout_seconds=float(arguments.get("approval_timeout_seconds", 0.0)),
                http_url=arguments.get("http_url"),  # type: ignore[arg-type]
            )
        durable = await asyncio.to_thread(DBOS.get_workflow_status, run_id)
        if durable is None:
            raise WorkflowBackendUnavailableError(
                f"workflow {run_id} not durable immediately after start"
            )
        _record_event(
            "workflow.started", run_id=run_id, tenant_id=request.tenant_id, trace_id=request.trace_id
        )
        return WorkflowStartResult(run_id=run_id, status="started")

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        """恢复语义：DBOS 新进程 launch 时自动 startup recovery；此处返回当前投影。"""
        tenant_id, trace_id = self._run_meta(run_id)
        status = await self._status_or_raise(run_id)
        _record_event("workflow.resume.requested", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        return WorkflowRunStatus(run_id=run_id, status=self._map(status.status))

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        tenant_id, trace_id = self._run_meta(run_id)
        await asyncio.to_thread(DBOS.send, run_id, payload, f"{name}:{run_id}")
        _record_event("workflow.signal.sent", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        await asyncio.to_thread(DBOS.cancel_workflow, run_id)

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        status = await self._status_or_raise(run_id)
        return WorkflowRunStatus(run_id=run_id, status=self._map(status.status))

    async def await_result(self, run_id: str, timeout: float) -> Any:
        result = await asyncio.wait_for(
            asyncio.to_thread(DBOS.get_result, run_id), timeout=timeout
        )
        tenant_id, trace_id = self._run_meta(run_id)
        _record_event("workflow.result.observed", run_id=run_id, tenant_id=tenant_id, trace_id=trace_id)
        return result

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

    def set_resource_version(
        self, resource_id: str, version: str, *, is_latest: bool
    ) -> None:
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

    async def _status_or_raise(self, run_id: str) -> Any:
        status = await asyncio.to_thread(DBOS.get_workflow_status, run_id)
        if status is None:
            raise WorkflowRunNotFoundError(f"workflow run {run_id} not found")
        return status

    @staticmethod
    def _map(dbos_status: Any) -> str:
        value = getattr(dbos_status, "value", dbos_status)
        return str(value).upper()
