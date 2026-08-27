"""DBOS PoC worker 子进程入口（TASK-003；真实进程边界测试用）。

用法（`python -m tests.workflow_poc.dbos_worker <mode> ...`，cwd=backend）：

- `start`：engine.start 启动 workflow → 打印 `STARTED <run_id>` → 阻塞等结果 → `COMPLETED`
- `recover`：launch（触发 DBOS startup recovery）→ 轮询目标 run 至终态 → `COMPLETED`
- `worker`：launch + queue 监听常驻 → 打印 `READY-<index>`（S-06 多 worker）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from fluxion.runtime.workflow import WorkflowStartRequest

from tests.workflow_poc.dbos_app import (
    DBOSWorkflowEngine,
    launch_dbos,
    run_id_for,
)

WORKFLOW_ID = "poc-durable"
TERMINAL_STATUSES = {"SUCCESS", "ERROR", "CANCELLED"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DBOS PoC worker")
    sub = parser.add_subparsers(dest="mode", required=True)

    start = sub.add_parser("start", help="启动一个 workflow 并等待完成")
    start.add_argument("--tenant", required=True)
    start.add_argument("--trace", required=True)
    start.add_argument("--args-json", default="{}", help="workflow arguments JSON")
    start.add_argument("--await-timeout", type=float, default=300.0)

    recover = sub.add_parser("recover", help="launch 触发恢复并轮询目标 run 至终态")
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--timeout", type=float, default=60.0)

    worker = sub.add_parser("worker", help="常驻 worker（queue 监听）")
    worker.add_argument("--index", type=int, default=0)
    worker.add_argument("--idle-seconds", type=float, default=300.0)
    return parser


async def _mode_start(args: argparse.Namespace) -> int:
    engine = DBOSWorkflowEngine()
    arguments = json.loads(args.args_json)
    execution_id = str(arguments.get("execution_id", f"exec-{int(time.time())}"))
    request = WorkflowStartRequest(
        workflow_id=WORKFLOW_ID,
        tenant_id=args.tenant,
        user_id="user-poc",
        execution_id=execution_id,
        trace_id=args.trace,
        arguments=arguments,
    )
    result = await engine.start(request)
    print(f"STARTED {result.run_id}", flush=True)
    try:
        await engine.await_result(result.run_id, timeout=args.await_timeout)
    except Exception as error:  # noqa: BLE001 — 进程边界：任何失败都以非零码上报
        print(f"FAILED {result.run_id} {type(error).__name__}: {error}", flush=True)
        return 1
    print(f"COMPLETED {result.run_id}", flush=True)
    return 0


async def _mode_recover(args: argparse.Namespace) -> int:
    engine = DBOSWorkflowEngine()  # launch() 内置 startup recovery
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = await engine.get_status(args.run_id)
        print(f"STATUS {args.run_id} {status.status}", flush=True)
        if status.status in TERMINAL_STATUSES:
            if status.status == "SUCCESS":
                print(f"COMPLETED {args.run_id}", flush=True)
                return 0
            print(f"FAILED {args.run_id} {status.status}", flush=True)
            return 1
        await asyncio.sleep(0.5)
    print(f"RECOVERY_TIMEOUT {args.run_id}", flush=True)
    return 1


async def _mode_worker(args: argparse.Namespace) -> int:
    DBOSWorkflowEngine()
    launch_dbos()
    print(f"READY-{args.index}", flush=True)
    await asyncio.sleep(args.idle_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "start":
        return asyncio.run(_mode_start(args))
    if args.mode == "recover":
        return asyncio.run(_mode_recover(args))
    return asyncio.run(_mode_worker(args))


if __name__ == "__main__":
    sys.exit(main())
