"""TASK-005 worker E2E fixtures：worker 场景定义 + 装配 + DBOS sysdb 辅助。

capability executor 复用 `graph_fixtures._capability_dispatcher`（真实副作用执行器，
psycopg 直写 `fluxion_workflow` 库的 `wf_test_records`，非 mock）：
- `echo`：回显（S-01 quick-flow / S-05 幂等）；
- `stamp`：写业务记录 + 睡眠 + 回填 finished_at（S-02 crash-flow 断点、
  S-06 queue-flow 慢 step 让双 worker 分摊可观测）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg

from fluxion.registry.sqlalchemy_store import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.workflow import WorkflowRunStatus
from fluxion.runtime.workflow_dbos import (
    DBOS_APP_NAME,
    DBOS_QUEUE_NAME,
    _map_status,
    set_definition_provider,
    set_reference_releaser,
    set_reference_store,
    set_sync_definition_resolver,
)
from fluxion.runtime.workflow_projection import (
    WorkflowRunProjectionWriter,
    ensure_workflow_run_table,
    release_workflow_active_references,
    set_projection_writer,
)
from tests.workflow_runtime.graph_fixtures import (
    ensure_business_tables,
    ensure_database,
    install_fixture_executors,
    list_records,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
WORKER_MODULE = "fluxion.cli.workflow_worker"
WORKER_BOOTSTRAP = "tests.workflow_runtime.worker_fixtures:install_worker_bootstrap"


def worker_db_url() -> str:
    return os.environ.get(
        "FLUXION_WORKFLOW_TEST_DB_URL",
        "postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow",
    )

# ---------------------------------------------------------------------------
# worker 场景定义（definitions: {workflow_id: {version: spec}}）
# ---------------------------------------------------------------------------

WORKER_DEFINITIONS: dict[str, dict[str, dict[str, str]]] = {
    # S-01/S-05：单 echo 节点，毫秒级完成；幂等键 (tenant, run, node) 恰 1 行。
    "quick-flow": {
        "1": {
            "name": "quick-flow",
            "steps": [
                {
                    "id": "echo",
                    "type": "capability",
                    "capability_ref": "skill:echo@1",
                    "input": {"greeting": "{{ input.greeting }}"},
                }
            ],
        }
    },
    # S-02：3 个 chained stamp（各 0.6s）。step_a/step_b 快速提交 → kill 断点；
    # 恢复后已完成 step 不重跑（executions==1），仅 step_c 重执行。
    "crash-flow": {
        "1": {
            "name": "crash-flow",
            "steps": [
                {
                    "id": "step_a",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.6},
                },
                {
                    "id": "step_b",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "depends_on": ["step_a"],
                    "input": {"seconds": 0.6},
                },
                {
                    "id": "step_c",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "depends_on": ["step_b"],
                    "input": {"seconds": 0.6},
                },
            ],
        }
    },
    # S-06：单 stamp 4.0s——首批（4 任务）被 worker-0 并发认领后 4s 内在飞；第二批
    # enqueue 时 worker-0 仍 busy（并发 4 已满）⇒ worker-1 的 poll 认领第二批。
    "queue-flow": {
        "1": {
            "name": "queue-flow",
            "steps": [
                {
                    "id": "slow",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 4.0},
                }
            ],
        }
    },
    # S-08：prepare（stamp，断点可观测）→ approve（human_task，无 timeout 无限等待）
    # → finalize（stamp）。worker 在审批挂起时 SIGKILL，重启后 send(approve signal)
    # 唤醒并继续；finalize 证明审批后节点跨重启执行。
    "approval-flow": {
        "1": {
            "name": "approval-flow",
            "steps": [
                {
                    "id": "prepare",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
                {
                    "id": "approve",
                    "type": "human_task",
                    "depends_on": ["prepare"],
                    "assignee": "user:alice",
                    "message": "审批测试",
                },
                {
                    "id": "finalize",
                    "type": "capability",
                    "depends_on": ["approve"],
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
            ],
        }
    },
    # S-09：before（stamp）→ hold（wait 6s durable timer）→ after（stamp）。
    # worker 在 wait 睡眠中被 SIGKILL，重启后按原始 deadline 触发（不重算 sleep）。
    "wait-flow": {
        "1": {
            "name": "wait-flow",
            "steps": [
                {
                    "id": "before",
                    "type": "capability",
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
                {
                    "id": "hold",
                    "type": "wait",
                    "depends_on": ["before"],
                    "duration_seconds": 6.0,
                },
                {
                    "id": "after",
                    "type": "capability",
                    "depends_on": ["hold"],
                    "capability_ref": "skill:stamp@1",
                    "input": {"seconds": 0.2},
                },
            ],
        }
    },
    # human_task 超时路径：timeout_seconds=2，无 signal → recv 超时 → 按 fail policy
    # 终态 ERROR（非永久挂起）。驱动进程/worker 均不 send。
    "approval-timeout-flow": {
        "1": {
            "name": "approval-timeout-flow",
            "steps": [
                {
                    "id": "review",
                    "type": "human_task",
                    "assignee": "role:admin",
                    "message": "超时审批",
                    "timeout_seconds": 2.0,
                }
            ],
        }
    },
}


def install_worker_bootstrap(database_url: str) -> None:
    """worker/驱动进程装配：真实 fixture executor + 覆盖全部 worker 场景的 provider。

    provider 是 Registry 读路径的真实形态（tenant + workflow_id + version → spec），
    由进程装配注入（`fluxion.cli.workflow_worker --bootstrap` 加载；S-01/S-05 驱动
    进程在 pytest 内直接调用）。
    """

    async def provider(tenant_id: str, workflow_id: str, version: str) -> Mapping[str, object]:
        spec = WORKER_DEFINITIONS[workflow_id][version]
        if spec is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        return spec

    def sync_resolver(tenant_id: str, workflow_id: str, version: str) -> Mapping[str, object]:
        # P0-1：解释器 subworkflow 走 sync resolver（DBOS 独立 loop 不能调 async provider）
        spec = WORKER_DEFINITIONS[workflow_id][version]
        if spec is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        return spec

    ensure_database(database_url)
    ensure_business_tables(database_url)
    install_fixture_executors()
    set_definition_provider(provider)
    set_sync_definition_resolver(sync_resolver)


def install_registry_worker_bootstrap(database_url: str) -> None:
    """S-03/S-07 worker 装配：Registry-backed provider + active ref store/releaser。

    provider 是真实 Registry 读路径（`store.recall_pinned` 按 pinned 版本精确回读，
    不 resolve latest——RULE-P3-02），并把每次解析坐标打印 `PROVIDER_RESOLVE
    <workflow_id> <version>` 供测试断言；worker 进程装配 `set_reference_store` +
    `set_reference_releaser` 使 `DbosWorkflowEngine.start` 能 acquire、
    worker 终态能 release active refs（TASK-007，与测试进程同 PG 库共享
    `active_references`）。
    """

    # registry store 用 asyncpg 驱动（SQLAlchemy `postgresql://` 默认 psycopg2，非本项目
    # 声明依赖）；DBOS sysdb 走 psycopg v3（同一 `--database-url`）。同库不同驱动连接。
    store = PostgreSQLRegistryStore(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))

    async def provider(tenant_id: str, workflow_id: str, version: str) -> Mapping[str, object]:
        definition = await store.recall_pinned(
            ResourceKind.WORKFLOW, workflow_id, tenant_id=tenant_id, version=version
        )
        print(f"PROVIDER_RESOLVE {workflow_id} {version}", flush=True)
        return definition.spec_json

    ensure_database(database_url)
    ensure_business_tables(database_url)
    ensure_workflow_run_table(database_url)  # TASK-008：投影表幂等 DDL（CREATE IF NOT EXISTS）
    install_fixture_executors()
    set_definition_provider(provider)

    def sync_resolver(tenant_id: str, workflow_id: str, version: str) -> dict[str, object]:
        # P0-1：解释器 subworkflow 在 DBOS 独立 loop 解析子定义，须走 sync psycopg
        #（async SQLAlchemy engine "different loop"）。语义同 `recall_pinned`：拒绝 DRAFT。
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT spec_json FROM resource_definitions "
                "WHERE tenant_id = %s AND kind = 'workflow' AND resource_id = %s "
                "AND version = %s AND status != 'draft'",
                (tenant_id, workflow_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        spec = row[0]
        if isinstance(spec, str):
            return json.loads(spec)
        return spec

    set_sync_definition_resolver(sync_resolver)
    set_reference_store(store)
    # releaser 走 sync psycopg 路径（解释器在 DBOS 独立 event loop，不能调 async
    # SQLAlchemy engine；P0-2 终态释放统一 sync）
    set_reference_releaser(
        lambda **kwargs: release_workflow_active_references(database_url, **kwargs)
    )
    set_projection_writer(WorkflowRunProjectionWriter(database_url))


def purge_stale_enqueued(
    database_url: str, queue_name: str = DBOS_QUEUE_NAME
) -> None:
    """清理 queue 遗留 ENQUEUED 行（上次中断运行的残留），避免被新 worker 误认领。"""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM dbos.workflow_status WHERE queue_name = %s AND status = 'ENQUEUED'",
            (queue_name,),
        )


def purge_stale_workflows(database_url: str) -> None:
    """清理上次中断运行的残留 PENDING/ENQUEUED workflow（测试隔离用）。

    残留 PENDING run 会在后续 worker 的 startup recovery 被重新执行、重写投影
    （S-11/B-02 精确计数断言会误报）；测试 setup 时清理（当前无在飞 run，安全），
    不触碰 DBOS 系统表的运行期语义。
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM dbos.workflow_status WHERE status IN ('PENDING', 'ENQUEUED')"
        )


def wait_for_records(
    database_url: str,
    run_id: str,
    predicate: Callable[[dict[str, dict[str, Any]]], bool],
    *,
    timeout: float,
    description: str,
) -> dict[str, dict[str, Any]]:
    """轮询 `wf_test_records`（run_id 内全部 node 行）直至 predicate 满足；超时失败。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = {r["node_id"]: r for r in list_records(database_url, run_id)}
        if predicate(records):
            return records
        time.sleep(0.2)
    raise AssertionError(
        f"wait_for_records timeout after {timeout}s: {description}; records={records}"
    )


# ---------------------------------------------------------------------------
# 独立 worker 子进程封装（S-02/S-06/S-08/S-09 共享）：真实 `fluxion-workflow-worker`
# ---------------------------------------------------------------------------


class WorkerProcess:
    """spawn `python -m fluxion.cli.workflow_worker` 子进程；后台线程读 stdout。

    提供等待标记行（`STARTED`/`READY-N`/`COMPLETED`/`RUN_FAILED`）与 SIGKILL
    （S-02/S-08/S-09 崩溃恢复场景），是独立 worker 真实边界的载体。
    """

    def __init__(
        self,
        args: list[str],
        *,
        extra_env: dict[str, str] | None = None,
        timeout: float = 30.0,
        bootstrap: str = WORKER_BOOTSTRAP,
    ) -> None:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        base = [
            sys.executable,
            "-m",
            WORKER_MODULE,
            "--database-url",
            worker_db_url(),
            "--bootstrap",
            bootstrap,
        ]
        self.proc = subprocess.Popen(
            [*base, *args],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        if self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def wait_for(self, marker: str, *, timeout: float) -> float:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if any(line.startswith(marker) for line in self.lines):
                return time.monotonic() - started
            returncode = self.proc.poll()
            if returncode is not None:
                time.sleep(0.2)
                if any(line.startswith(marker) for line in self.lines):
                    return time.monotonic() - started
                raise AssertionError(
                    f"worker exited rc={returncode} before {marker!r}; output:\n{self.output}"
                )
            time.sleep(0.05)
        raise AssertionError(
            f"worker timeout after {timeout}s waiting for {marker!r}; output:\n{self.output}"
        )

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


class WorkflowTestClient:
    """驱动进程纯 client（DBOSClient，无 launch/queue 消费）：状态轮询 + signal。

    生产拓扑（design §4.1，rule 13）：API/Console 进程只做 client 侧
    start/signal/cancel/status，不参与 workflow 执行与恢复。DBOSClient 只连 sysdb、
    不 launch——launched 的 DBOS 无条件消费 `_dbos_internal_queue`（_queue.py
    "Always listen to the internal queue"），recovery 会把 PENDING workflow re-enqueue
    到该 queue，此时驱动进程会与 recover worker 抢 dequeue（dequeue 不过滤
    executor_id，实测 executor 被抢成 local，kill/recover 失效、S-09 计时被破坏）。
    纯 client 让 recover worker 成为唯一可恢复方，kill/recover 真实生效。
    """

    def __init__(self, database_url: str) -> None:
        from dbos import DBOSClient

        self._client = DBOSClient(
            system_database_url=database_url,
            application_name=DBOS_APP_NAME,
            use_listen_notify=False,
        )

    def get_status(self, run_id: str) -> WorkflowRunStatus:
        handle = self._client.retrieve_workflow(run_id)
        return WorkflowRunStatus(run_id=run_id, status=_map_status(handle.get_status()))

    def signal(self, run_id: str, name: str, payload: object) -> None:
        # 与 DbosWorkflowEngine.signal 同一 topic 契约：f"{name}:{run_id}"
        self._client.send(run_id, payload, f"{name}:{run_id}")

    def close(self) -> None:
        self._client.destroy()
