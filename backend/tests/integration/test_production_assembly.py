"""TASK-006（Phase 6）Phase 5 生产装配集成测试（FEAT-P6-06，S-10）。

真实边界（不得 mock）：
- 真实 PostgreSQL（fluxion_test）：Secret 落 PG 密文（AES-256-GCM）+ Registry；
- 真实 MinIO（docker fluxion-test-minio）：S3 artifact put/get + artifact_metadata；
- 真实 DBOS sysdb（fluxion_workflow 库）+ 真实 worker 子进程：Operations 端点
  返回真实队列/worker 状态（非空数组）；
- 真实 HTTP（ASGITransport + 统一 envelope）：enforced Release Gate publish
  fail-closed（38_001）；
- production InMemory fail-fast：守卫在装配路径生效。

无真实依赖（PG/MinIO 不可达）时 skip——绝不伪造 GREEN。
k8s Pod 部署级验证见 test_k8s_deployment.py（FLUXION_K8S_TEST=1 门控）。
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.workflow_runtime.worker_fixtures import (
    WorkerProcess,
    install_worker_bootstrap,
    purge_stale_enqueued,
    worker_db_url,
)

from fluxion.api.production_bundle import (
    ProductionS3Config,
    create_production_bundle_app,
)
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.workflow import WorkflowPinnedRef, WorkflowStartRequest
from fluxion.runtime.workflow_dbos import DBOS_QUEUE_NAME, DbosWorkflowEngine
from fluxion.services.production_profile import ProductionProfileError

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


def _minio_endpoint() -> str:
    return os.environ.get("FLUXION_MINIO_ENDPOINT", "http://localhost:9000")


def _minio_available() -> bool:
    parsed = urlparse(_minio_endpoint())
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 9000), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL（fluxion_test）不可达（S-10 真实边界）"
)


# ---------------------------------------------------------------------------
# helpers（与 test_operations_api 同模式：真实 worker 子进程 + 真实 DBOS 引擎）
#


async def _run_workflow(engine: DbosWorkflowEngine, execution_id: str) -> str:
    from dbos import DBOS

    request = WorkflowStartRequest(
        workflow_id="quick-flow",
        tenant_id="tenant-s10",
        user_id="user-s10",
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        arguments={"greeting": "ops"},
        pinned=(WorkflowPinnedRef(kind="workflow", id="quick-flow", version="1"),),
    )
    run_id = (await engine.start(request)).run_id
    await asyncio.wait_for(asyncio.to_thread(DBOS.get_result, run_id), timeout=60.0)
    return run_id


async def _enqueue_only(engine: DbosWorkflowEngine, execution_id: str) -> None:
    request = WorkflowStartRequest(
        workflow_id="quick-flow",
        tenant_id="tenant-s10",
        user_id="user-s10",
        execution_id=execution_id,
        trace_id=f"trace-{execution_id}",
        arguments={"greeting": "queued"},
        pinned=(WorkflowPinnedRef(kind="workflow", id="quick-flow", version="1"),),
    )
    await engine.start(request)


@pytest.fixture
async def bundle(
    tmp_path: Path,
) -> AsyncGenerator[tuple[object, object], None]:
    """构造生产装配 bundle（真实 PG + MinIO + DBOS sysdb DSN）。

    返回 (app, assembly)。MinIO 不可达时 s3 装配跳过（artifact 测试单独门控）。
    """
    console_dist = tmp_path / "console"
    chat_dist = tmp_path / "chat"
    console_dist.mkdir()
    chat_dist.mkdir()
    (console_dist / "index.html").write_text("<html>console</html>")
    (chat_dist / "index.html").write_text("<html>chat</html>")

    app = create_production_bundle_app(
        registry_dsn=_PG_DSN,
        master_key=os.urandom(32),
        console_dist=console_dist,
        chat_dist=chat_dist,
        sysdb_dsn=worker_db_url(),
        s3_config=ProductionS3Config(
            endpoint=_minio_endpoint(),
            access_key=os.environ.get("FLUXION_MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("FLUXION_MINIO_SECRET_KEY", "minioadmin"),
            bucket="fluxion-s10-artifacts",
        )
        if _minio_available()
        else None,
    )
    assembly = app.state.assembly
    await assembly.initialize()
    try:
        yield app, assembly
    finally:
        await assembly.close()


class TestS10ProductionAssembly:
    async def test_secret_persisted_encrypted_in_pg(
        self, bundle: tuple[object, object]
    ) -> None:
        """S-10：Secret 经生产装配落 PG 密文（AES-256-GCM），resolve 往返一致。"""
        _, assembly = bundle
        secret_store = assembly.secret_store
        plaintext = f"sk-secret-{uuid.uuid4().hex}"
        ref = await secret_store.put("tenant-s10", "model", plaintext)
        assert ref.startswith("secret://tenant-s10/model@")

        resolved = await secret_store.resolve(ref)
        assert resolved.value == plaintext

        engine: AsyncEngine = assembly.store.engine
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT nonce, ciphertext, key_id, cipher_version "
                         "FROM secret_credentials WHERE ref = :ref"),
                    {"ref": ref},
                )
            ).fetchone()
        assert row is not None, "secret 行未落 PG"
        nonce, ciphertext, key_id, cipher_version = row
        assert bytes(nonce) and len(bytes(nonce)) == 12
        assert plaintext.encode("utf-8") not in bytes(ciphertext), "PG 中出现明文"
        assert key_id and cipher_version == "aes-256-gcm-v1"

    @pytest.mark.skipif(not _minio_available(), reason="MinIO 不可达（S-10 真实边界）")
    async def test_artifact_put_get_with_s3_and_metadata(
        self, bundle: tuple[object, object]
    ) -> None:
        """S-10：artifact 经生产装配落 S3/MinIO + artifact_metadata 表。"""
        _, assembly = bundle
        artifact_store = assembly.artifact_store
        assert artifact_store is not None, "生产装配应包含 S3CompatibleArtifactStore"
        key = f"s10-{uuid.uuid4().hex[:8]}"
        value = b"s10-production-assembly-bytes"
        await artifact_store.put("tenant-s10", "reports", key, value)
        assert await artifact_store.get("tenant-s10", "reports", key) == value

        engine: AsyncEngine = assembly.store.engine
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT size, sha256, status FROM artifact_metadata "
                        "WHERE tenant_id = 'tenant-s10' AND namespace = 'reports' "
                        "AND key = :key AND status = 'active'"
                    ),
                    {"key": key},
                )
            ).fetchone()
        assert row is not None, "artifact_metadata 行缺失"
        size, sha256, status = row
        assert size == len(value) and status == "active" and len(sha256) == 64

    async def test_release_gate_enforced_publish_fail_closed(
        self, bundle: tuple[object, object]
    ) -> None:
        """S-10：production 装配 release_gate_enforced=True——无 gate 参数 publish
        fail-closed（38_001），资源保持 draft。"""
        app, assembly = bundle
        store = assembly.store
        tag = uuid.uuid4().hex[:8]
        resource_id = f"runtime-s10-{tag}"
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id=resource_id,
                tenant_id="tenant-s10",
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"request_timeout_ms": 1000, "max_retries": 2},
            )
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://production"
        ) as client:
            response = await client.post(
                f"/api/v1/resources/runtime_profile/{resource_id}/versions/1:publish",
                json={},
                headers={"X-Tenant-ID": "tenant-s10", "X-Actor-ID": "admin-s10"},
            )
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == 38_001
        assert "强制" in body["message"]

        resource = await store.get(
            ResourceKind.RUNTIME_PROFILE,
            resource_id,
            tenant_id="tenant-s10",
            version="1",
        )
        assert resource is not None and resource.status is ResourceStatus.DRAFT

    async def test_operations_endpoints_return_real_dbos_state(
        self, bundle: tuple[object, object]
    ) -> None:
        """S-10：Operations 端点装配真实 DBOS sysdb——worker 子进程 + ENQUEUED
        深度 → queues/workers 返回真实状态（非空数组）。"""
        app, _ = bundle
        db_url = worker_db_url()
        install_worker_bootstrap(db_url)
        purge_stale_enqueued(db_url, DBOS_QUEUE_NAME)

        worker = WorkerProcess(
            ["serve", "--index", "0", "--idle-seconds", "60"],
            extra_env={"DBOS__VMID": "worker-s10"},
            timeout=60.0,
        )
        tag = uuid.uuid4().hex[:6]
        try:
            worker.wait_for("READY-0", timeout=60.0)
            # 1) worker 存活时跑一条 → executor_id=worker-s10 出现（worker 视图数据源）
            engine = DbosWorkflowEngine(
                database_url=db_url, listen_queues=[], enqueue_start=True
            )
            await _run_workflow(engine, f"s10-{tag}-a")
            # 2) worker 停止后再 enqueue → 停留 ENQUEUED（depth 证据）
            worker.stop()
            await _enqueue_only(engine, f"s10-{tag}-b")

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://production"
            ) as client:
                headers = {"X-Tenant-ID": "tenant-s10", "X-Actor-ID": "admin-s10"}
                queues_response = await client.get(
                    "/api/v1/operations/queues", headers=headers
                )
                workers_response = await client.get(
                    "/api/v1/operations/workers", headers=headers
                )
        finally:
            if worker.proc.poll() is None:
                worker.stop()

        assert queues_response.status_code == 200, queues_response.text
        queues_body = queues_response.json()
        assert queues_body["code"] == 0
        fluxion_queue = next(
            (q for q in queues_body["data"] if q["name"] == DBOS_QUEUE_NAME), None
        )
        assert fluxion_queue is not None, f"缺 fluxion-workflow queue：{queues_body['data']}"
        assert fluxion_queue["depth"] >= 1, "ENQUEUED 深度应 ≥1"

        assert workers_response.status_code == 200, workers_response.text
        workers_body = workers_response.json()
        assert workers_body["code"] == 0
        assert isinstance(workers_body["data"], list)
        assert len(workers_body["data"]) >= 1, "workers 应返回真实实例视图（非空）"

    async def test_production_inmemory_fail_fast(self) -> None:
        """S-10：production profile 下 InMemory 唯一实现 → fail-fast 拒绝。"""
        from fluxion.runtime.secrets import LocalEncryptedSecretStore
        from fluxion.runtime.tracing import InMemoryTraceStore
        from fluxion.services.approval_app import InMemoryApprovalStore
        from fluxion.services.eval_app import InMemoryEvalRunStore
        from fluxion.services.production_profile import verify_production_assembly

        with pytest.raises(ProductionProfileError):
            verify_production_assembly(
                secret_store=LocalEncryptedSecretStore(master_key=b"k" * 32),
                trace_store=InMemoryTraceStore(),
                approval_store=InMemoryApprovalStore(),
                eval_run_store=InMemoryEvalRunStore(),
            )

    async def test_production_bundle_rejects_non_postgres_dsn(
        self, tmp_path: Path
    ) -> None:
        """S-10：production 装配拒绝非 PostgreSQL DSN（fail-fast，不静默降级 SQLite）。"""
        with pytest.raises(ProductionProfileError, match="PostgreSQL"):
            create_production_bundle_app(
                registry_dsn="sqlite+aiosqlite:///./dev.db",
                master_key=os.urandom(32),
                console_dist=tmp_path,
                chat_dist=tmp_path,
            )
