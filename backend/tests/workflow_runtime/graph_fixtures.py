"""TASK-004 解释器 E2E fixtures：业务表 + 真实 capability executor + 场景定义。

capability executor 是真实副作用执行器（psycopg 直写 `fluxion_workflow`
库的 `wf_test_records` 业务表，幂等键 (tenant, run, node)），非 mock：
- `echo`：回显 input（含 `{{ }}` 插值结果）；
- `tier`：返回 {"tier": input["tier"]}（condition/switch 路由数据源）；
- `flaky`：首写业务记录后抛异常 → DBOS step durable retry → 二次执行成功，
  记录仍恰 1 行（E-03）；
- `slow`：按 input["seconds"] 睡眠（S-04 超时 / S-10 并发观测）；
- `stamp`：记录 start/finished 时间戳（S-10 并发窗口断言）。
"""

from __future__ import annotations

import time
from typing import Any

import psycopg

from fluxion.runtime.workflow_graph import CapabilityNodeRequest, set_capability_executor

BUSINESS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS wf_test_records (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '',
    executions INT NOT NULL DEFAULT 1,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION,
    PRIMARY KEY (tenant_id, run_id, node_id)
);
"""


def ensure_database(db_url: str) -> None:
    """幂等创建业务库（本地 PG：mmuser 为 superuser，可经 postgres 库建库）。"""
    from urllib.parse import urlsplit

    dbname = urlsplit(db_url).path.lstrip("/")
    admin_url = db_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE "{dbname}"')


def ensure_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(BUSINESS_TABLE_DDL)


def reset_business_tables(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE wf_test_records")


def list_records(db_url: str, run_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT node_id, payload, executions, started_at, finished_at "
            "FROM wf_test_records WHERE run_id = %s ORDER BY node_id",
            (run_id,),
        ).fetchall()
    return [
        {
            "node_id": row[0],
            "payload": row[1],
            "executions": row[2],
            "started_at": row[3],
            "finished_at": row[4],
        }
        for row in rows
    ]


def _business_write(
    request: CapabilityNodeRequest, payload: str
) -> int:
    """幂等写业务记录；返回该 (run, node) 的累计执行次数（DBOS retry 可见）。"""
    started = time.time()
    with psycopg.connect(_resolve_db_url()) as conn:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO wf_test_records "
                "(tenant_id, run_id, node_id, payload, executions, started_at, finished_at) "
                "VALUES (%s, %s, %s, %s, 1, %s, %s) "
                "ON CONFLICT (tenant_id, run_id, node_id) DO UPDATE "
                "SET executions = wf_test_records.executions + 1, "
                "payload = EXCLUDED.payload, finished_at = EXCLUDED.finished_at "
                "RETURNING executions",
                (request.tenant_id, request.run_id, request.node_id, payload, started, time.time()),
            ).fetchone()
    return int(row[0]) if row else 1


def _business_finish(request: CapabilityNodeRequest) -> None:
    """工作结束后回填 finished_at（stamp 并发窗口测量：started 前/后分别采样）。"""
    with psycopg.connect(_resolve_db_url()) as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE wf_test_records SET finished_at = %s "
                "WHERE tenant_id = %s AND run_id = %s AND node_id = %s",
                (time.time(), request.tenant_id, request.run_id, request.node_id),
            )


def _resolve_db_url() -> str:
    import os

    return os.environ.get(
        "FLUXION_WORKFLOW_TEST_DB_URL",
        "postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow",
    )


async def _capability_dispatcher(request: CapabilityNodeRequest) -> object:
    resource_id = request.capability_ref.split(":", 1)[1].split("@", 1)[0]
    if resource_id == "echo":
        _business_write(request, str(request.input))
        return dict(request.input)
    if resource_id == "tier":
        _business_write(request, str(request.input))
        return {"tier": request.input.get("tier", "std")}
    if resource_id == "flaky":
        executions = _business_write(request, "flaky-done")
        if executions == 1:
            raise RuntimeError("flaky capability first execution fails (E-03)")
        return {"flaky": "recovered", "executions": executions}
    if resource_id == "slow":
        _business_write(request, "slow-started")
        await _sleep_no_busy(float(request.input.get("seconds", 1)))
        return {"slept": request.input.get("seconds", 1)}
    if resource_id == "stamp":
        _business_write(request, str(request.input))
        await _sleep_no_busy(float(request.input.get("seconds", 0.6)))
        _business_finish(request)  # 睡眠结束后回填 finished_at：并发窗口可测
        return {"stamped": request.node_id, "seconds": request.input.get("seconds", 0.6)}
    raise ValueError(f"unknown capability fixture: {resource_id}")


async def _sleep_no_busy(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def install_fixture_executors() -> None:
    """runner/worker 进程装配：全部 fixture capability 走 skill: 前缀。"""
    set_capability_executor("skill", _capability_dispatcher)


# ---------------------------------------------------------------------------
# 场景定义（definitions: {workflow_id: {version: spec}}）
# ---------------------------------------------------------------------------

S10_DEFINITIONS: dict[str, dict[str, str]] = {
    "onboarding": {
        "1": {
            "name": "onboarding",
            "steps": [
                {
                    "id": "fetch",
                    "type": "capability",
                    "capability_ref": "skill:tier@1",
                    "input": {"tier": "{{ input.tier }}"},
                },
                {
                    "id": "branch",
                    "type": "condition",
                    "depends_on": ["fetch"],
                    "expression": '{{ fetch.output.tier }} == "gold"',
                    "then": ["gold_setup"],
                    "else": ["std_setup"],
                },
                {
                    "id": "gold_setup",
                    "type": "capability",
                    "depends_on": ["branch"],
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
                {
                    "id": "std_setup",
                    "type": "capability",
                    "depends_on": ["branch"],
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
                {
                    "id": "fanout",
                    "type": "parallel",
                    "depends_on": ["branch"],
                    "branches": [
                        {"branch_id": "left", "node_ids": ["par_a"]},
                        {"branch_id": "right", "node_ids": ["par_b"]},
                    ],
                    "join_policy": "all",
                },
                {
                    "id": "par_a",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.6},
                },
                {
                    "id": "par_b",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.6},
                },
                {
                    "id": "brief",
                    "type": "transform",
                    "depends_on": ["fanout"],
                    "source": "{{ fetch.output }}",
                    "transform": "tier={{ fetch.output.tier }}",
                },
                {
                    "id": "child",
                    "type": "subworkflow",
                    "depends_on": ["brief"],
                    "workflow_ref": "child-flow@1",
                    "input": {"greeting": "hello"},
                },
                {
                    "id": "notify",
                    "type": "capability",
                    "capability_ref": "skill:echo@1",
                    "depends_on": ["child"],
                    "input": {"done": "{{ child.output.outputs.child_step.greeting }}"},
                },
            ],
        }
    },
    "child-flow": {
        "1": {
            "name": "child-flow",
            "steps": [
                {
                    "id": "child_step",
                    "type": "capability",
                    "capability_ref": "skill:echo@1",
                    "input": {"greeting": "{{ input.greeting }}"},
                }
            ],
        }
    },
}

S04_DEFINITIONS: dict[str, dict[str, str]] = {
    "timeout-flow": {
        "1": {
            "name": "timeout-flow",
            "steps": [
                {
                    "id": "too_slow",
                    "type": "capability",
                    "capability_ref": "skill:slow@1",
                    "timeout_ms": 300,
                    "input": {"seconds": 5},
                }
            ],
        }
    },
}

E03_DEFINITIONS: dict[str, dict[str, str]] = {
    "retry-flow": {
        "1": {
            "name": "retry-flow",
            "steps": [
                {
                    "id": "flaky_step",
                    "type": "capability",
                    "capability_ref": "skill:flaky@1",
                    "input": {},
                }
            ],
        }
    },
}
