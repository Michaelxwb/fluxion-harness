"""TASK-005（Phase 6）生产运行边界（FEAT-P6-05 ②④⑤，S-08 / E-07 / E-08）。

- S-08[E2E]：停 Console → 已发布 Agent 继续执行（Runtime 不调 Console API
  获取配置 truth，G7/ARCH-14）——真实边界：Runtime API 独立进程（真实 PG，
  Console 进程自始不存在）+ 真实 HTTP 执行；
- E-07[integration]：production profile 下 InMemory Trace/Approval/Eval/Secret
  唯一实现 → fail-fast 拒绝（P0-5，REQ-OBS-002/REQ-SEC-006）；
- E-08[integration]：production + RuntimeScheduler 本地 `_tasks` 实现 →
  fail-fast 拒绝启用（P0-4，REQ-SCH-001）。
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.scheduler import RuntimeScheduler, SchedulerProfileError
from fluxion.services.production_profile import (
    ProductionProfileError,
    verify_production_assembly,
)
from tests.runtime_helpers import publish_resource, seed_model_definition

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(_PG_DSN)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


class TestS08ConsoleIndependence:
    def test_s08_runtime_executes_without_console(self, store: PostgreSQLRegistryStore) -> None:
        """S-08[E2E]：Console 进程不存在 → 已发布 Agent 执行不受影响。

        真实边界：Runtime API 独立进程（`fluxion serve` 非 dev 模式——进程内只有
        Runtime API 路由，无 Console app）+ 真实 PG Registry。配置 truth 来自
        Registry（G7/ARCH-14），全程无 Console 进程。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（S-08 真实边界）")

        import asyncio

        tenant_id = f"tenant-s08-{uuid.uuid4().hex[:8]}"
        agent_id = "s08-agent"
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _seed_agent(store, tenant_id, agent_id)
        )

        port = _free_port()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_BACKEND_DIR / "src") + os.pathsep + env.get("PYTHONPATH", "")
        fluxion_cli = str(Path(sys.executable).parent / "fluxion")
        # Runtime API 进程（非 --dev：无 Console bundle、无静态前端）
        runtime_proc = subprocess.Popen(
            [
                fluxion_cli, "serve",
                "--host", "127.0.0.1", "--port", str(port),
                "--registry-dsn", _PG_DSN,
            ],
            cwd=str(_BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_healthy(port, timeout=25.0)

            # Console 进程自始不存在（G7：配置 truth = Registry，非 Console API）
            # 1) Console 路由在 Runtime API 上不存在（404——非转发、非代理）
            console_response = httpx.get(
                f"http://127.0.0.1:{port}/api/v1/resources?resource_type=runtime_profile",
                timeout=10.0,
            )
            assert console_response.status_code == 404, (
                "Runtime API 不得暴露 Console 资源路由（配置 truth 不经 Console）"
            )

            # 2) 已发布 Agent 执行成功（真实 HTTP Execution）
            run_response = httpx.post(
                f"http://127.0.0.1:{port}/internal/v1/runtime-profiles/{agent_id}/runs",
                json={
                    "tenant_id": tenant_id,
                    "user_id": "user-s08",
                    "session_id": "s-console-down",
                    "input": "console-down-ping",
                },
                timeout=30.0,
            )
            assert run_response.status_code == 200, run_response.text
            body = run_response.json()
            assert body["code"] == 0, body
        finally:
            if runtime_proc.poll() is None:
                runtime_proc.send_signal(signal.SIGKILL)
                runtime_proc.wait(timeout=10)


async def _seed_agent(store: PostgreSQLRegistryStore, tenant_id: str, agent_id: str) -> None:
    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=agent_id,
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    # ADR-A008：agent.model_policy → ModelDefinition（model.dev.echo）→ dev.echo；
    # 真实执行走 ContextResolver，ModelDefinition 缺失会 fail-closed（422）。
    await seed_model_definition(store, tenant_id=tenant_id, provider_id="dev.echo")
    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=agent_id,
        version="1",
        spec={
            "name": "s08-agent",
            "system_prompt": "你是产品助手。",
            "owner": "builder",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
        },
    )


def _wait_healthy(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"Runtime API {timeout}s 内未健康")


class TestE07ProductionFailFast:
    def test_e07_inmemory_unique_implementations_fail_fast(self) -> None:
        """E-07[integration]：production profile 下 InMemory 唯一实现逐项 fail-fast。

        P0-5：Trace/Approval/Eval/Secret 任一 InMemory/Local 实现装配 →
        启动明确拒绝（不静默降级）；durable 装配放行。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（E-07 真实边界）")

        from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
        from fluxion.repositories import (
            PostgresApprovalStore,
            PostgresEvalRunStore,
            PostgresTraceStore,
        )
        from fluxion.runtime.secrets import LocalEncryptedSecretStore
        from fluxion.runtime.tracing import InMemoryTraceStore
        from fluxion.services.approval_app import InMemoryApprovalStore
        from fluxion.services.eval_app import InMemoryEvalRunStore

        durable = {
            "secret_store": PostgresEncryptedSecretStore(
                engine=_engine(), master_key=b"k" * 32, key_id="e07"
            ),
            "trace_store": PostgresTraceStore(engine=_engine()),
            "approval_store": PostgresApprovalStore(engine=_engine()),
            "eval_run_store": PostgresEvalRunStore(engine=_engine()),
        }

        # durable 装配放行（production adapter 语义）
        verify_production_assembly(**durable)

        # 逐项替换为 InMemory → fail-fast（每项独立触发，错误信息点名）
        for key, inmemory in [
            ("trace_store", InMemoryTraceStore()),
            ("approval_store", InMemoryApprovalStore()),
            ("eval_run_store", InMemoryEvalRunStore()),
            ("secret_store", LocalEncryptedSecretStore(master_key=b"k" * 32)),
        ]:
            with pytest.raises(ProductionProfileError, match=key.split("_")[0]):
                verify_production_assembly(**{**durable, key: inmemory})


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(_PG_DSN)


class TestE08SchedulerGuard:
    def test_e08_local_scheduler_production_fail_fast(self) -> None:
        """E-08[integration]：production + 本地 scheduler → 构造即 fail-fast 拒绝。

        P0-4/REQ-SCH-001：本地 `_tasks` 实现仅 test/dev 放行。
        """
        from fluxion.runtime.agent import AgentRuntime

        runtime = AgentRuntime.__new__(AgentRuntime)  # 构造守卫不触达 runtime 内部
        with pytest.raises(SchedulerProfileError, match="production"):
            RuntimeScheduler(runtime, profile="production")

        # test/dev profile 放行
        scheduler = RuntimeScheduler(runtime, profile="dev")
        assert scheduler.local_execution_state_count == 0
