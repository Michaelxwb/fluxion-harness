"""TASK-004 解释器 runner：独立子进程承载 DBOS + 解释器 + fixture executors。

用法（cwd=backend，PYTHONPATH=backend）：
`python -m tests.workflow_runtime.graph_runner run --scenario s10 ...`

输出协议（单行 marker，pytest 侧解析）：
- `RUN_STARTED <run_id>`
- `RUN_RESULT <json>`（status/result/elapsed_ms）
- `RUN_FAILED <json>`（status/error/elapsed_ms）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Mapping

from fluxion.runtime.workflow import WorkflowPinnedRef, WorkflowStartRequest
from fluxion.runtime.workflow_dbos import DbosWorkflowEngine, set_definition_provider
from tests.workflow_runtime import graph_fixtures

SCENARIOS: dict[str, dict[str, object]] = {
    "s10": {
        "definitions": graph_fixtures.S10_DEFINITIONS,
        "workflow_id": "onboarding",
        "version": "1",
        "arguments": {"tier": "gold"},
    },
    "s04": {
        "definitions": graph_fixtures.S04_DEFINITIONS,
        "workflow_id": "timeout-flow",
        "version": "1",
        "arguments": {},
    },
    "e03": {
        "definitions": graph_fixtures.E03_DEFINITIONS,
        "workflow_id": "retry-flow",
        "version": "1",
        "arguments": {},
    },
}


def _resolve_db_url() -> str:
    import os

    return os.environ.get(
        "FLUXION_WORKFLOW_TEST_DB_URL",
        "postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow",
    )


async def _run_scenario(args: argparse.Namespace) -> int:
    scenario = SCENARIOS[args.scenario]
    definitions: Mapping[str, Mapping[str, str]] = scenario["definitions"]  # type: ignore[assignment]
    db_url = _resolve_db_url()
    graph_fixtures.ensure_database(db_url)
    graph_fixtures.ensure_business_tables(db_url)
    graph_fixtures.reset_business_tables(db_url)
    graph_fixtures.install_fixture_executors()

    async def provider(tenant_id: str, workflow_id: str, version: str) -> Mapping[str, object]:
        spec = definitions[workflow_id][version]
        if spec is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        return spec

    def sync_resolver(tenant_id: str, workflow_id: str, version: str) -> Mapping[str, object]:
        # P0-1：subworkflow 在 DBOS 独立 loop 走 sync resolver（async provider 不可用）
        spec = definitions[workflow_id][version]
        if spec is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        return spec

    set_definition_provider(provider)
    from fluxion.runtime.workflow_dbos import set_sync_definition_resolver

    set_sync_definition_resolver(sync_resolver)
    engine = DbosWorkflowEngine(database_url=db_url)
    request = WorkflowStartRequest(
        workflow_id=str(scenario["workflow_id"]),
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id=args.execution_id,
        trace_id=f"trace-{args.execution_id}",
        arguments=dict(scenario["arguments"]),  # type: ignore[arg-type]
        pinned=(
            WorkflowPinnedRef(
                kind="workflow", id=str(scenario["workflow_id"]), version=str(scenario["version"])
            ),
        ),
    )
    started = time.monotonic()
    start_result = await engine.start(request)
    print(f"RUN_STARTED {start_result.run_id}", flush=True)
    try:
        result = await engine.await_result(start_result.run_id, timeout=args.await_timeout)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        print(
            "RUN_RESULT "
            + json.dumps(
                {"run_id": start_result.run_id, "status": "SUCCESS", "result": result, "elapsed_ms": elapsed_ms},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        return 0
    except Exception as error:  # noqa: BLE001 — 进程边界：失败形态完整上报
        elapsed_ms = round((time.monotonic() - started) * 1000)
        try:
            status = await engine.get_status(start_result.run_id)
            dbos_status = status.status
        except Exception:  # noqa: BLE001 — 状态查询失败时以异常名为准
            dbos_status = type(error).__name__
        print(
            "RUN_FAILED "
            + json.dumps(
                {
                    "run_id": start_result.run_id,
                    "status": dbos_status,
                    "error": f"{type(error).__name__}: {error}",
                    "elapsed_ms": elapsed_ms,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="workflow graph interpreter runner")
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run", help="启动场景 workflow 并等待结果")
    run.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    run.add_argument("--execution-id", required=True)
    run.add_argument("--await-timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "run":
        return asyncio.run(_run_scenario(args))
    return 2


if __name__ == "__main__":
    sys.exit(main())
