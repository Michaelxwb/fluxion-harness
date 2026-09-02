"""TASK-002（Phase 6）Chaos——Runtime 组（FEAT-P6-02，design §3.2 套件布局）。

S-02[E2E] + E-01[integration]（RULE-P6-02：不得 mock Runtime 真实进程 / Store）。

- S-02：SIGKILL 真实 Runtime API 进程（uvicorn 子进程，真实 PG Registry）→ 重启
  → 恢复耗时（进程启动 → 首个成功 Execution）P95≤30s（NFR-P6-REC-01）；kill
  前后 Snapshot digest 一致率=100%（NFR-P6-CONSIST-01，架构规则 28）；
- E-01：L1 缓存 flush → miss 后降级 L2（Registry 真实回读），数据无损。

D1 选型：进程级故障注入（pytest + subprocess kill/restart），部署级语义由
S-07 真实 k8s Gate（FEAT-P6-05）承接。
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
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import publish_resource, seed_model_definition

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
_BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

pytestmark = pytest.mark.chaos_runtime


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


class RuntimeProcess:
    """真实 Runtime API 进程（`fluxion serve`，非 dev bundle——Runtime API only）。"""

    def __init__(self, port: int) -> None:
        self.port = port
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_BACKEND_DIR / "src") + os.pathsep + env.get("PYTHONPATH", "")
        fluxion_cli = str(Path(sys.executable).parent / "fluxion")
        self.proc = subprocess.Popen(
            [
                fluxion_cli,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--registry-dsn",
                _PG_DSN,
            ],
            cwd=str(_BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)

    def wait_healthy(self, timeout: float = 25.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"Runtime 进程退出: {self.proc.returncode}"
                )
            try:
                response = httpx.get(f"http://127.0.0.1:{self.port}/healthz", timeout=2.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise AssertionError(f"Runtime 进程 {timeout}s 内未健康")

    async def run_execution(self, tenant_id: str, agent_id: str, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"http://127.0.0.1:{self.port}/internal/v1/runtime-profiles/{agent_id}/runs",
                json={
                    "tenant_id": tenant_id,
                    "user_id": "user-chaos",
                    "session_id": session_id,
                    "input": "chaos-ping",
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == 0, body
        return dict(body["data"])


@pytest.fixture
async def seeded_store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(_PG_DSN)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _seed_agent(store: PostgreSQLRegistryStore, tenant_id: str, agent_id: str) -> None:
    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id=agent_id,
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    # ADR-A008：agent.model_policy → ModelDefinition（model.dev.echo）→
    # provider dev.echo；S-02 的 digest/真实进程执行都走全量解析（fail-closed）。
    await seed_model_definition(store, tenant_id=tenant_id, provider_id="dev.echo")
    await publish_resource(
        store,
        tenant_id=tenant_id,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id=agent_id,
        version="1",
        spec={
            "name": "chaos-agent",
            "system_prompt": "你是产品助手。",
            "owner": "builder",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
        },
    )


class TestS02RuntimeChaos:
    async def test_s02_kill_runtime_process_recovery_p95_and_digest_consistency(
        self, seeded_store: PostgreSQLRegistryStore
    ) -> None:
        """S-02[E2E]：kill Runtime 进程 → 重启 → 恢复 P95≤30s + digest 一致率=100%。

        真实边界：真实 Runtime API 进程（SIGKILL）+ 真实 PG Registry；无 mock。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（S-02 真实边界）")

        tenant_id = f"tenant-chaos-rt-{uuid.uuid4().hex[:8]}"
        agent_id = "chaos-agent"
        await _seed_agent(seeded_store, tenant_id, agent_id)

        selector = ResolverSelector(tenant_id=tenant_id, agent_id=agent_id, user_id="user-chaos")
        baseline_digest = await _digest(seeded_store, selector)

        # 3 轮 kill → 重启，每轮恢复耗时（进程启动 → 首个成功 Execution）
        recoveries: list[float] = []
        digests_after: list[str] = []
        # review P2 补强：kill 前捕获被杀进程的**真实执行产物**（HTTP 响应字段——
        # 解析事实 runtime_profile_version/output），重启后对拍——不再只依赖
        # 测试进程自算 digest
        runtime = RuntimeProcess(_free_port())
        try:
            runtime.wait_healthy()
            baseline_payload = await runtime.run_execution(tenant_id, agent_id, "warmup")
            for round_index in range(3):
                runtime.kill()

                started = time.monotonic()
                runtime = RuntimeProcess(runtime.port)
                runtime.wait_healthy()
                payload = await runtime.run_execution(
                    tenant_id, agent_id, f"recovery-{round_index}"
                )
                recoveries.append(time.monotonic() - started)
                # 被杀进程产物 vs 重启后产物：解析事实跨重启一致
                assert (
                    payload["runtime_profile_version"]
                    == baseline_payload["runtime_profile_version"]
                ), "重启后 runtime_profile_version 漂移"
                assert payload["output"] == baseline_payload["output"], (
                    "重启后执行产物漂移（解析事实不一致）"
                )

                digests_after.append(await _digest(seeded_store, selector))
        finally:
            runtime.kill()

        # NFR-P6-REC-01：恢复 P95≤30s（3 样本以 max 记，严于 P95）
        worst = max(recoveries)
        assert worst <= 30.0, f"恢复耗时最差 {worst:.1f}s > 30s（P95 预算）: {recoveries}"

        # NFR-P6-CONSIST-01：kill 前后 digest 一致率=100%
        assert all(digest == baseline_digest for digest in digests_after), (
            f"digest 漂移: baseline={baseline_digest}, after={digests_after}"
        )


async def _digest(store: PostgreSQLRegistryStore, selector: ResolverSelector) -> str:
    resolver = ContextResolver(store)
    result = await resolver.resolve(selector, session_id="s-chaos-digest")
    assert result.snapshot.snapshot_digest
    return str(result.snapshot.snapshot_digest)


class TestE01CacheFlush:
    async def test_e01_cache_flush_degrades_to_l2(self, seeded_store: PostgreSQLRegistryStore) -> None:
        """E-01[integration]：L1 flush → miss 降级 L2（Registry 回读），数据无损。

        真实边界：真实 TenantResourceCache（L1）+ 真实 PG Registry（L2）——
        RevisionAwareResourceResolver 在 cache flush 后从 Registry 重新解析。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（E-01 真实边界）")

        from fluxion.resources import ResourceStatus
        from fluxion.resources.cache import TenantResourceCache
        from fluxion.runtime.hot_reload import RevisionAwareResourceResolver

        tenant_id = f"tenant-chaos-cache-{uuid.uuid4().hex[:8]}"
        agent_id = "cache-agent"
        await _seed_agent(seeded_store, tenant_id, agent_id)

        cache = TenantResourceCache(ttl_seconds=60.0)
        resolver = RevisionAwareResourceResolver(seeded_store, cache=cache)

        first = await resolver.resolve_resource(
            tenant_id, ResourceKind.AGENT_DEFINITION, agent_id
        )
        assert first.status is ResourceStatus.PUBLISHED

        # L1 命中（cache 内有该资源）
        hit = await resolver.resolve_resource(
            tenant_id, ResourceKind.AGENT_DEFINITION, agent_id
        )
        assert hit == first

        # L1 flush（真实 cache 失效——故障注入）
        cache.invalidate_tenant(tenant_id)

        # miss 后降级 L2：Registry 真实回读，数据无损
        after_flush = await resolver.resolve_resource(
            tenant_id, ResourceKind.AGENT_DEFINITION, agent_id
        )
        assert after_flush == first, "cache flush 后 L2 回读应得到同一资源（数据无损）"
        assert after_flush.version == first.version
