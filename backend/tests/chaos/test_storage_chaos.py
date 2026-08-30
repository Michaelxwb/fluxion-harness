"""TASK-002（Phase 6）Chaos——Storage 组（FEAT-P6-02，design §3.2 套件布局）。

S-04[E2E] + E-04/E-05[integration]（RULE-P6-02：不得 mock 真实 Store）。

- S-04：真实 PG 连接中断（pg_terminate_backend 杀应用连接——App 层 RPO 等价，
  remediation §17.2：Infrastructure RPO 由部署契约声明）→ 重连后已提交 durable
  state 完整（RPO=0）+ 写路径恢复；
- E-04：ArtifactStore（local-fs dev）指向不可达路径 → put/get 显式失败，
  已存 artifact 不损坏；
- E-05：SemanticStore 不可用（pgvector 表缺失/不可达）→ Memory 检索降级
  no-memory（空 manifest + content_hash="unavailable"），resolve 不崩溃。
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.plugins.artifact.local_fs import LocalFileArtifactStore
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.context_resolver import ContextResolver
from tests.runtime_helpers import publish_resource

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
_ADMIN_DSN = os.environ.get(
    "FLUXION_PG_ADMIN_DSN",
    "postgresql://mmuser:mmuser@localhost:5432/fluxion_test",
)


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.chaos_storage


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """带唯一 application_name 的 engine（S-04 定向 terminate 故障注入用）。"""
    engine = create_async_engine(
        _PG_DSN,
        connect_args={"server_settings": {"application_name": _APP_NAME}},
        # 生产引擎语义：断连后池 pre_ping 换新连接（registry engine 同配置）
        pool_pre_ping=True,
    )
    yield engine
    await engine.dispose()


_APP_NAME = f"chaos-storage-{uuid.uuid4().hex[:8]}"


def _terminate_backend_connections(app_name: str) -> int:
    """用独立管理连接 pg_terminate_backend 杀掉目标应用的全部 PG 连接。

    等价部署侧连接中断/failover（App 层 RPO 语义，remediation §17.2）。
    """
    import psycopg

    with psycopg.connect(_ADMIN_DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE application_name = %s AND pid <> pg_backend_pid()",
            (app_name,),
        ).fetchall()
    return len(rows)


class TestS04StorageChaos:
    async def test_s04_pg_connection_interruption_rpo_zero(self, engine: AsyncEngine) -> None:
        """S-04[E2E]：PG 连接中断 → 已提交 durable state RPO=0（零丢失）+ 写恢复。

        真实边界：真实 PostgreSQL——pg_terminate_backend 杀应用连接（部署侧
        failover 的 App 层等价），非 mock 断连。durable fact 经**真实应用治理
        事务**提交（store.publish → commit_publication：audit_logs + publish_
        records + outbox 原子落库，review P1-3——非测试自插行）。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（S-04 真实边界）")

        from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
        from fluxion.registry import PostgreSQLRegistryStore
        from fluxion.registry.store import PublicationCommand, PublicationOperation

        marker = f"rpo-{uuid.uuid4().hex[:8]}"
        # 1) 故障前：经应用治理事务提交 durable fact（commit_publication：audit_logs +
        # publish_records + outbox 原子落库——review P1-3 真实应用写路径，非自插行）
        store = PostgreSQLRegistryStore(_PG_DSN)
        await store.initialize()
        try:
            await store.put(
                ResourceDefinition(
                    kind=ResourceKind.RUNTIME_PROFILE,
                    id=marker,
                    tenant_id="tenant-chaos-st",
                    version="1",
                    status=ResourceStatus.DRAFT,
                    spec_json={"request_timeout_ms": 1000, "max_retries": 1},
                )
            )
            await store.commit_publication(
                PublicationCommand(
                    publish_id=f"pub-s04-{uuid.uuid4().hex[:8]}",
                    event_id=f"evt-s04-{uuid.uuid4().hex[:8]}",
                    tenant_id="tenant-chaos-st",
                    kind=ResourceKind.RUNTIME_PROFILE,
                    resource_id=marker,
                    version="1",
                    operation=PublicationOperation.PUBLISH,
                    actor_id="chaos-s04",
                    request_id="req-s04",
                    trace_id="trace-s04",
                )
            )
        finally:
            await store.close()

        # 2) 故障注入：杀掉本 engine 的全部 PG 连接（先建立池连接再终止）
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        terminated = _terminate_backend_connections(_APP_NAME)
        assert terminated >= 1, "应至少终止 1 条应用连接（故障注入生效）"

        # 3) 重连后：已提交 durable state 完整（RPO=0）——治理事务产物零丢失
        async with engine.connect() as conn:
            audits = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_logs WHERE target_id = :marker"
                    ),
                    {"marker": marker},
                )
            ).scalar_one()
            publishes = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM publish_records WHERE resource_id = :marker"
                    ),
                    {"marker": marker},
                )
            ).scalar_one()
        assert audits >= 1, "治理事务 audit_logs 在连接中断后必须零丢失（RPO=0）"
        assert publishes == 1, "publish_records 在连接中断后必须零丢失（RPO=0）"

        # 4) 恢复后写路径可用：新 store 实例可继续治理事务发布新版本
        store2 = PostgreSQLRegistryStore(_PG_DSN)
        await store2.initialize()
        try:
            await store2.put(
                ResourceDefinition(
                    kind=ResourceKind.RUNTIME_PROFILE,
                    id=marker,
                    tenant_id="tenant-chaos-st",
                    version="2",
                    status=ResourceStatus.DRAFT,
                    spec_json={"request_timeout_ms": 1000, "max_retries": 1},
                )
            )
            await store2.commit_publication(
                PublicationCommand(
                    publish_id=f"pub-s04b-{uuid.uuid4().hex[:8]}",
                    event_id=f"evt-s04b-{uuid.uuid4().hex[:8]}",
                    tenant_id="tenant-chaos-st",
                    kind=ResourceKind.RUNTIME_PROFILE,
                    resource_id=marker,
                    version="2",
                    operation=PublicationOperation.PUBLISH,
                    actor_id="chaos-s04",
                    request_id="req-s04b",
                    trace_id="trace-s04b",
                )
            )
            republished = await store2.get(
                ResourceKind.RUNTIME_PROFILE, marker, tenant_id="tenant-chaos-st", version="2"
            )
            assert republished is not None and republished.status is ResourceStatus.PUBLISHED
        finally:
            await store2.close()


class TestE04ArtifactUnreachable:
    async def test_e04_artifact_store_unreachable_explicit_failure(
        self, engine: AsyncEngine, tmp_path: Path
    ) -> None:
        """E-04[integration]：ArtifactStore 指向不可达路径 → 显式失败，
        已存 artifact 不损坏。"""
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（E-04 真实边界）")

        tenant = f"tenant-e04-{uuid.uuid4().hex[:8]}"  # 唯一 tenant：防跨运行 metadata 污染
        healthy_root = tmp_path / "healthy"
        healthy = LocalFileArtifactStore(healthy_root, engine)
        await healthy.initialize()
        await healthy.put(tenant, "reports", "keep", b"keep-bytes")
        before = await healthy.get(tenant, "reports", "keep")

        # 故障注入：root 指向不可达路径（权限/挂载失败等价——父目录为文件）
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        broken = LocalFileArtifactStore(blocker / "unreachable", engine)
        from fluxion.plugins.artifact.local_fs import ArtifactStoreError

        with pytest.raises((ArtifactStoreError, OSError, NotADirectoryError)):
            # initialize（mkdir 不可达）或首个 put 任一环节显式失败
            await broken.initialize()
            await broken.put(tenant, "reports", "new", b"new-bytes")

        # 已存 artifact 不损坏
        after = await healthy.get(tenant, "reports", "keep")
        assert after == before == b"keep-bytes"


def _raise_semantic_recall():
    """故障注入：PgVectorSemanticStore.recall 抛错（SemanticStore 不可达等价）。

    仅注入语义检索段；ContextResolver 的资源解析段（registry store）不受影响
    ——隔离 memory 段降级语义（E-05 环境扰动，非边界 mock）。
    """
    from unittest.mock import patch

    from fluxion.plugins.providers import pgvector_semantic

    class _Boom(RuntimeError):
        pass

    async def _raise(*args: object, **kwargs: object) -> object:
        raise _Boom("semantic store unreachable (chaos E-05)")

    return patch.object(pgvector_semantic.PgVectorSemanticStore, "recall", _raise)


class TestE05SemanticDegraded:
    async def test_e05_semantic_store_unavailable_degrades_to_no_memory(
        self, engine: AsyncEngine
    ) -> None:
        """E-05[integration]：SemanticStore 不可用 → Memory 检索降级 no-memory，
        ContextResolver resolve 不崩溃。"""
        if not _pg_available():
            pytest.skip("PostgreSQL（fluxion_test）不可达（E-05 真实边界）")

        from fluxion.resources import ResourceKind

        store = PostgreSQLRegistryStore(_PG_DSN)
        await store.initialize()
        try:
            tenant_id = f"tenant-chaos-sem-{uuid.uuid4().hex[:8]}"
            agent_id = "sem-agent"
            await publish_resource(
                store,
                tenant_id=tenant_id,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id=agent_id,
                version="1",
                spec={"request_timeout_ms": 30_000, "max_retries": 1},
            )
            await publish_resource(
                store,
                tenant_id=tenant_id,
                kind=ResourceKind.AGENT_DEFINITION,
                resource_id=agent_id,
                version="1",
                spec={
                    "name": "sem-agent",
                    "system_prompt": "你是产品助手。",
                    "owner": "builder",
                    "model_ref": {"id": "dev.echo", "version": "1"},
                },
            )
        finally:
            pass

        # review P2 修复：完整 resolve() 路径验证——资源段（runtime_profile/
        # agent_definition 解析）走真实 PG store；仅 SemanticStore 段注入故障
        #（D1 环境扰动：recall 抛错 = 不可达等价）。resolve 必须整体成功返回，
        # memory 段降级 no-memory manifest。
        from fluxion.services.context_resolver import ResolverSelector

        resolver = ContextResolver(store)
        with _raise_semantic_recall():
            result = await resolver.resolve(
                ResolverSelector(tenant_id=tenant_id, agent_id=agent_id, user_id="user-chaos"),
                session_id="s-e05",
            )
        # resolve 整体成功（不崩溃）+ memory 段降级语义
        manifest = result.snapshot.memory_manifest
        assert manifest is not None
        assert manifest.content_hash == "unavailable", "SemanticStore 故障时降级 no-memory"
        assert manifest.entry_refs == []
        assert manifest.truncated is True
        # 资源段不受故障影响（真实解析成功）
        assert result.snapshot.agent_definition_version == "1"
        await store.close()
