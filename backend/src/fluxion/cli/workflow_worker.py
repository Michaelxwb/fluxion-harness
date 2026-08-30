"""fluxion-workflow-worker 独立执行进程入口（design §4.1 部署架构）。

Deployment 形态：≥2 副本，唯一执行进程。API/Console 进程持 DbosWorkflowEngine
只做 client 侧 start/signal/cancel/status（stateless，rule 13）。

职责：
- `DBOS.launch()`（含 startup recovery，S-02）+ `listen_queues`（S-06）；
- 后台线程 `register_queue`：`DBOS.register_queue` 要求非 async 上下文
  （check_async 硬 raise，PoC 已验证）；
- `worker_concurrency` 有界（PoC 4，实测 8 任务 4/4 分摊）防单 worker 全认领；
- launch 有界等待（规则 18）：DBOS 对不可达 backend 内部无限重试，由
  `--launch-timeout` 兜底，超时/失败映射 `WorkflowBackendUnavailableError` 非 hang。

CLI 模式（全部经真实 DbosWorkflowEngine + DBOS + PG）：
- `serve`：queue 消费常驻（生产 Deployment；S-06 双 worker 分摊）；
- `start`：启动一个 workflow 并阻塞等待结果（S-02 被 SIGKILL 的执行进程）；
- `recover`：新进程 launch（触发 startup recovery）+ 轮询目标 run 至终态
  （S-02 恢复进程，SLO-WF-02 recovery P95≤60s）。

`--bootstrap <module>:<attr>` 装配 capability/agent executor + definition
provider：`<attr>` 为 `(database_url: str) -> None` 可调用，注入 Registry 读路径
（业务在 Capability，解释器不感知 Provider，RULE-fluxion-workflow-001）。

用法（cwd=backend，PYTHONPATH=backend）：
  python -m fluxion.cli.workflow_worker serve --index 0
  python -m fluxion.cli.workflow_worker start --workflow-id X --execution-id Y
  python -m fluxion.cli.workflow_worker recover --run-id X
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import threading
import time
from collections.abc import Callable

from dbos import DBOS

from fluxion.errors.workflow import WorkflowBackendUnavailableError
from fluxion.runtime.workflow import WorkflowPinnedRef, WorkflowStartRequest
from fluxion.runtime.workflow_dbos import (
    DBOS_QUEUE_NAME,
    DEFAULT_LAUNCH_TIMEOUT_SECONDS,
    DEFAULT_WORKER_CONCURRENCY,
    PROJECTION_STATUS_BY_DBOS,
    TERMINAL_STATUSES,
    DbosWorkflowEngine,
    configure_dbos,
    register_workflow_queue,
)
from fluxion.runtime.workflow_projection import get_projection_writer

DEFAULT_AWAIT_TIMEOUT_SECONDS = 120.0

Bootstrap = Callable[[str], None]


def _load_bootstrap(spec: str) -> Bootstrap | None:
    """`module:attr` → 装配 callable；`<attr>` 为 `(database_url: str) -> None`。"""
    if not spec:
        return None
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    bootstrap: object = getattr(module, attr)
    if not callable(bootstrap):
        raise SystemExit(f"bootstrap {spec!r} is not callable")
    return bootstrap  # callable() 已收窄 object → Callable[..., Any]（兼容 Bootstrap）


def _bootstrap(args: argparse.Namespace) -> Bootstrap | None:
    bootstrap = _load_bootstrap(args.bootstrap)
    if bootstrap is not None:
        bootstrap(args.database_url)
    return bootstrap


# ---------------------------------------------------------------------------
# serve：queue 消费常驻（生产 Deployment；S-06）
# ---------------------------------------------------------------------------


async def _mode_serve(args: argparse.Namespace) -> int:
    _bootstrap(args)
    configure_dbos(args.database_url)
    DBOS.listen_queues([DBOS_QUEUE_NAME])  # 必须在 DBOS.launch() 之前调用
    launched = threading.Event()
    launch_error: BaseException | None = None

    def _launch() -> None:
        nonlocal launch_error
        try:
            DBOS.launch()
        except BaseException as error:  # noqa: BLE001 — 线程边界：等待方统一映射
            launch_error = error
        finally:
            launched.set()

    threading.Thread(target=_launch, name="dbos-launch", daemon=True).start()
    if not launched.wait(args.launch_timeout):
        raise WorkflowBackendUnavailableError(
            f"DBOS launch did not complete within {args.launch_timeout}s"
        )
    if launch_error is not None:
        raise WorkflowBackendUnavailableError(f"DBOS launch failed: {launch_error}")

    threading.Thread(
        target=register_workflow_queue,
        kwargs={
            "database_url": args.database_url,
            "worker_concurrency": args.worker_concurrency,
        },
        name="dbos-register-queue",
        daemon=True,
    ).start()
    print(f"READY-{args.index}", flush=True)
    # idle_seconds=0 → 生产常驻（Phase 6 review P0-1：此前纯墙钟计时，默认 3600
    # 导致 worker 每小时 exit 0 重启——即使正在消费 workflow 也定时退出）。
    # 正值保留给测试基建控制 worker 生命期（--idle-seconds N）。
    if args.idle_seconds <= 0:
        while True:
            await asyncio.sleep(1.0)
    deadline = time.monotonic() + args.idle_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
    return 0


# ---------------------------------------------------------------------------
# start：启动一个 workflow 并阻塞等待（S-02 可 SIGKILL 的执行进程 / S-05 首启）
# ---------------------------------------------------------------------------


async def _mode_start(args: argparse.Namespace) -> int:
    _bootstrap(args)
    engine = DbosWorkflowEngine(database_url=args.database_url)
    request = WorkflowStartRequest(
        workflow_id=args.workflow_id,
        tenant_id=args.tenant,
        user_id=args.user,
        execution_id=args.execution_id,
        trace_id=f"trace-{args.execution_id}",
        arguments=json.loads(args.args_json),
        pinned=(
            WorkflowPinnedRef(
                kind="workflow", id=args.workflow_id, version=args.version
            ),
        ),
    )
    start_result = await engine.start(request)
    run_id = start_result.run_id
    print(f"STARTED {run_id}", flush=True)
    try:
        result = await engine.await_result(run_id, timeout=args.await_timeout)
    except Exception as error:  # noqa: BLE001 — 进程边界：失败形态完整上报
        # TASK-007 terminal GC：await_result 失败路径（终态 ERROR/CANCELLED）释放
        # active refs；超时/backend 故障等非终态不释放（run 仍可能存活/可恢复）。
        try:
            status = await engine.get_status(run_id)
        except Exception:  # noqa: BLE001 — 进程边界：不因 GC 掩盖原始失败
            status = None
        if status is not None and status.status in TERMINAL_STATUSES:
            await engine.release_run_references(run_id, tenant_id=request.tenant_id)
            _finish_projection(request.tenant_id, run_id, "failed")
        _emit_failed(run_id, error)
        return 1
    # TASK-007 terminal GC：终态 SUCCESS → 释放 active refs（GC 正确性，S-07）
    await engine.release_run_references(run_id, tenant_id=request.tenant_id)
    # TASK-008 terminal 投影：succeeded（解释器已写，此处幂等兜底）
    _finish_projection(request.tenant_id, run_id, "succeeded")
    print(
        "RUN_RESULT "
        + json.dumps({"run_id": run_id, "status": "SUCCESS", "result": result}, default=str),
        flush=True,
    )
    return 0


# ---------------------------------------------------------------------------
# recover：新进程 launch 触发 startup recovery + 轮询目标 run（S-02）
# ---------------------------------------------------------------------------


async def _mode_recover(args: argparse.Namespace) -> int:
    _bootstrap(args)
    engine = DbosWorkflowEngine(database_url=args.database_url)  # launch 内置 recovery
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = await engine.get_status(args.run_id)
        if status.status in TERMINAL_STATUSES:
            # TASK-007 terminal GC：recover 观察到终态后释放被 kill worker 持有的
            # active refs（run 已终结，引用不应残留；S-03/S-07）
            await engine.release_run_references(args.run_id, tenant_id=args.tenant)
            # TASK-008 terminal 投影：recover 观察到的终态走 DBOS→投影词表映射
            # （P1-11：禁止 .lower()，词表外/超列宽）
            _finish_projection(
                args.tenant,
                args.run_id,
                PROJECTION_STATUS_BY_DBOS.get(status.status, "failed"),
            )
            if status.status == "SUCCESS":
                print(f"COMPLETED {args.run_id}", flush=True)
                return 0
            print(f"FAILED {args.run_id} {status.status}", flush=True)
            return 1
        await asyncio.sleep(0.5)
    print(f"RECOVERY_TIMEOUT {args.run_id}", flush=True)
    return 1


# ---------------------------------------------------------------------------
# 输出 / parser
# ---------------------------------------------------------------------------


def _emit_failed(run_id: str, error: BaseException) -> None:
    print(
        "RUN_FAILED "
        + json.dumps(
            {"run_id": run_id, "status": "ERROR", "error": f"{type(error).__name__}: {error}"},
            default=str,
        ),
        flush=True,
    )


def _finish_projection(tenant_id: str, run_id: str, status: str) -> None:
    """terminal 状态写投影（TASK-008）；writer 未装配 no-op；不掩盖原始失败。"""
    writer = get_projection_writer()
    if writer is None:
        return
    try:
        writer.finish_run(tenant_id=tenant_id, run_id=run_id, status=status)
    except Exception as error:  # noqa: BLE001 — 进程边界：投影失败不掩盖 workflow 结果
        print(f"PROJECTION_FAILED {run_id} {type(error).__name__}: {error}", flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fluxion-workflow-worker")
    parser.add_argument(
        "--database-url",
        default="postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow",
        help="DBOS/业务库 URL（生产经 WorkflowBackendSettings 配置注入）",
    )
    parser.add_argument(
        "--bootstrap",
        default="",
        help="`module:attr` 装配 executor + definition provider（`(url)->None`）",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    serve = sub.add_parser("serve", help="queue 消费常驻（生产 Deployment）")
    serve.add_argument("--index", type=int, default=0)
    serve.add_argument("--worker-concurrency", type=int, default=DEFAULT_WORKER_CONCURRENCY)
    # 0 = 常驻（生产 Deployment 默认；review P0-1：旧默认 3600 导致定时退出）
    serve.add_argument("--idle-seconds", type=float, default=0.0)
    serve.add_argument("--launch-timeout", type=float, default=DEFAULT_LAUNCH_TIMEOUT_SECONDS)

    start = sub.add_parser("start", help="启动 workflow 并等待结果")
    start.add_argument("--workflow-id", required=True)
    start.add_argument("--version", default="1")
    start.add_argument("--execution-id", required=True)
    start.add_argument("--tenant", default="tenant-a")
    start.add_argument("--user", default="user-a")
    start.add_argument("--args-json", default="{}")
    start.add_argument("--await-timeout", type=float, default=DEFAULT_AWAIT_TIMEOUT_SECONDS)

    recover = sub.add_parser("recover", help="launch 触发 recovery + 轮询目标 run")
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--tenant", default="tenant-a")
    recover.add_argument("--timeout", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "serve":
        return asyncio.run(_mode_serve(args))
    if args.mode == "start":
        return asyncio.run(_mode_start(args))
    return asyncio.run(_mode_recover(args))


if __name__ == "__main__":
    sys.exit(main())
