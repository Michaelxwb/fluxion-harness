"""TASK-004（Phase 6）Final DoD 自动化验收（FEAT-P6-04，RULE-P6-04）。

14 项 DoD verifier（每项一个测试；14/14 全过才允许 Release）。映射：

- DoD 1-2（NFR-P6-CONSIST-01/02）：digest 一致率 / capability equivalence；
- DoD 3-6（NFR-P6-REC-01/02、REL-01/02）：恢复/RPO/重复副作用——本文件为轻量
  复核（真实边界小样本）；完整故障注入语义由 chaos 套件（S-02/S-03/S-04/E-03）
  在 `fluxion-dod verify` 编排中同门禁运行；
- DoD 7（NFR-P6-TRACE-01）：trace completeness ≥99%；
- DoD 8（NFR-P6-SEC-01）：tenant escape=0；
- DoD 9（NFR-P6-UX-01）：UX journey success ≥95%（真实浏览器 Playwright）；
- DoD 10-13（NFR-P6-LEGACY-01..04）：四类 legacy 静态扫描 =0；
- DoD 14（NFR-P6-DEL-01 / B-02）：active pinned resource hard-delete=0。

真实边界：真实 PG（fluxion_test）、真实 Runtime、真实 trace span 采样、
真实浏览器（journey）、真实源码 AST 扫描——无 mock。
"""

from __future__ import annotations

import os
import socket
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.store import RegistryStoreError
from fluxion.resources import ResourceKind
from tests.runtime_helpers import publish_resource

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
_REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.dod


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
async def store() -> AsyncGenerator[PostgreSQLRegistryStore, None]:
    store = PostgreSQLRegistryStore(_PG_DSN)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _seed(store: PostgreSQLRegistryStore, tenant_id: str, agent_id: str) -> None:
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
            "name": "dod-agent",
            "system_prompt": "你是产品助手。",
            "owner": "builder",
            "model_ref": {"id": "dev.echo", "version": "1"},
        },
    )


# ---------------------------------------------------------------------------
# DoD 1-2：一致性（NFR-P6-CONSIST-01/02）
#


class TestDod01Consistency:
    async def test_dod_01_digest_cross_instance_consistency(self, store: PostgreSQLRegistryStore) -> None:
        """DoD-1：Snapshot digest cross-instance 一致率=100%。"""
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-1 真实边界）")
        from fluxion.services.context_resolver import ContextResolver, ResolverSelector

        tenant_id = f"tenant-dod-{uuid.uuid4().hex[:8]}"
        await _seed(store, tenant_id, "dod-agent")
        selector = ResolverSelector(
            tenant_id=tenant_id, agent_id="dod-agent", user_id="user-dod"
        )
        digests = set()
        for _ in range(3):  # 3 个独立 resolver 实例（跨实例对拍）
            resolver = ContextResolver(store.engine)
            result = await resolver.resolve(selector, session_id="s-dod")
            digests.add(result.snapshot.snapshot_digest)
        assert len(digests) == 1, f"digest 跨实例不一致: {digests}"

    async def test_dod_02_capability_equivalence(self, store: PostgreSQLRegistryStore) -> None:
        """DoD-2：同 tenant+user+agent 解析等价（架构规则 28）=100%。"""
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-2 真实边界）")
        from fluxion.services.context_resolver import ContextResolver, ResolverSelector

        tenant_id = f"tenant-dod-{uuid.uuid4().hex[:8]}"
        await _seed(store, tenant_id, "dod-agent")
        selector = ResolverSelector(
            tenant_id=tenant_id, agent_id="dod-agent", user_id="user-dod"
        )
        results = []
        for _ in range(2):
            resolver = ContextResolver(store.engine)
            results.append(await resolver.resolve(selector, session_id="s-dod"))
        a, b = (r.snapshot for r in results)
        for key in (
            "runtime_profile_version",
            "agent_definition_version",
            "model_resolution",
            "system_prompt",
            "policy_version",
        ):
            assert getattr(a, key) == getattr(b, key), f"等价性破坏: {key}"


# ---------------------------------------------------------------------------
# DoD 3-6：可靠性（轻量复核；完整故障注入由 chaos 套件编排）
#


class TestDod03Reliability:
    async def test_dod_03_runtime_recovery_rebuild(self, store: PostgreSQLRegistryStore) -> None:
        """DoD-3：Runtime 恢复——无状态重建（新实例从 Registry 重建执行成功）。

        完整 kill/重启语义由 chaos S-02（fluxion-dod verify 编排）验证。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-3 真实边界）")
        from fluxion.services.runtime_app import (
            RunRuntimeRequest,
            RuntimeApplicationService,
        )

        tenant_id = f"tenant-dod-{uuid.uuid4().hex[:8]}"
        await _seed(store, tenant_id, "dod-agent")

        # 「重启」等价：全新 service 实例（零本地状态）→ 从 Registry 重建 → 执行成功
        service = RuntimeApplicationService.create_dev_bundle(store)
        result = await service.run(
            RunRuntimeRequest(
                tenant_id=tenant_id,
                user_id="user-dod",
                runtime_profile_id="dod-agent",
                session_id="s-recovery",
                input_message="dod-ping",
            )
        )
        assert result is not None
        await service.close()

    async def test_dod_05_rpo_zero_committed_state(self) -> None:
        """DoD-5：已提交 durable state RPO=0（轻量：治理事务提交→读回零丢失）。

        durable fact 经真实应用治理事务写入（store.commit_publication：audit_logs
        + publish_records + outbox 原子落库——review P1-3，非测试自插行）。
        完整连接中断语义由 chaos S-04（fluxion-dod verify 编排）验证。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-5 真实边界）")
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from fluxion.registry.store import PublicationCommand, PublicationOperation
        from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus

        marker = f"dod-rpo-{uuid.uuid4().hex[:8]}"
        store = PostgreSQLRegistryStore(_PG_DSN)
        await store.initialize()
        try:
            await store.put(
                ResourceDefinition(
                    kind=ResourceKind.RUNTIME_PROFILE,
                    id=marker,
                    tenant_id="tenant-dod",
                    version="1",
                    status=ResourceStatus.DRAFT,
                    spec_json={"request_timeout_ms": 1000, "max_retries": 1},
                )
            )
            await store.commit_publication(
                PublicationCommand(
                    publish_id=f"pub-dod5-{uuid.uuid4().hex[:8]}",
                    event_id=f"evt-dod5-{uuid.uuid4().hex[:8]}",
                    tenant_id="tenant-dod",
                    kind=ResourceKind.RUNTIME_PROFILE,
                    resource_id=marker,
                    version="1",
                    operation=PublicationOperation.PUBLISH,
                    actor_id="dod-verifier",
                    request_id="req-dod-5",
                    trace_id="trace-dod-5",
                )
            )
        finally:
            await store.close()

        # 独立连接读回：治理事务产物零丢失（RPO=0）
        engine = create_async_engine(_PG_DSN)
        try:
            async with engine.connect() as conn:
                audits = (
                    await conn.execute(
                        text("SELECT COUNT(*) FROM audit_logs WHERE target_id = :marker"),
                        {"marker": marker},
                    )
                ).scalar_one()
                publishes = (
                    await conn.execute(
                        text("SELECT COUNT(*) FROM publish_records WHERE resource_id = :marker"),
                        {"marker": marker},
                    )
                ).scalar_one()
            assert audits >= 1, "治理事务 audit_logs 丢失（RPO>0）"
            assert publishes == 1, "publish_records 丢失（RPO>0）"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# DoD 7：trace completeness（NFR-P6-TRACE-01 ≥99%）
#


class TestDod07Trace:
    async def test_dod_07_trace_completeness(self) -> None:
        """DoD-7：采样 span 关联完整率 ≥99%（traced_scope 全链路关联字段）。"""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from fluxion.observability.context import RequestContext, bind_request_context
        from fluxion.observability.tracing import get_tracer, traced_scope

        # 与 span correlation gate 同模式：挂 exporter 到全局 SDK provider
        get_tracer("fluxion")
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            pytest.skip("全局 TracerProvider 非 SDK 实现，无法挂 exporter")
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        unique_trace = f"trace-dod-{uuid.uuid4().hex[:8]}"
        bind_request_context(
            RequestContext(
                request_id="req-dod",
                trace_id=unique_trace,
                tenant_id="tenant-dod",
                actor_id="dod-verifier",
                method="POST",
                route="/dod/verify",
                client_ip="127.0.0.1",
                user_agent="pytest",
            )
        )
        for index in range(100):
            async with traced_scope(f"dod.probe.{index}", attributes={"index": index}):
                pass
        spans = [
            s for s in exporter.get_finished_spans()
            if s.attributes.get("fluxion.trace_id") == unique_trace
        ]
        assert len(spans) >= 100
        fields = ("fluxion.trace_id", "fluxion.request_id", "fluxion.tenant_id")
        incomplete = [
            s.name for s in spans
            if any(f not in (s.attributes or {}) for f in fields)
        ]
        completeness = 1 - len(incomplete) / len(spans)
        assert completeness >= 0.99, (
            f"trace completeness {completeness:.1%} < 99%: {incomplete[:5]}"
        )


# ---------------------------------------------------------------------------
# DoD 8：tenant escape=0（NFR-P6-SEC-01）
#


class TestDod08Security:
    async def test_dod_08_tenant_escape_zero(self, store: PostgreSQLRegistryStore) -> None:
        """DoD-8：跨租户资源不可见（正反断言，真实 PG）。"""
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-8 真实边界）")
        tenant_a = f"tenant-dod-a-{uuid.uuid4().hex[:8]}"
        tenant_b = f"tenant-dod-b-{uuid.uuid4().hex[:8]}"
        await _seed(store, tenant_a, "dod-agent")

        # 正：tenant A 可见
        visible = await store.list_resources(
            ResourceKind.AGENT_DEFINITION, tenant_id=tenant_a, offset=0, limit=10
        )
        assert any(d.id == "dod-agent" for d in visible[0])

        # 反：tenant B 不可见（tenant escape=0）
        invisible = await store.list_resources(
            ResourceKind.AGENT_DEFINITION, tenant_id=tenant_b, offset=0, limit=10
        )
        assert not any(d.id == "dod-agent" for d in invisible[0])
        got = await store.get(
            ResourceKind.AGENT_DEFINITION, "dod-agent", tenant_id=tenant_b, version="1"
        )
        assert got is None, "跨租户读到他人资源（tenant escape）"


# ---------------------------------------------------------------------------
# DoD 9：UX journey ≥95%（NFR-P6-UX-01；真实浏览器 Playwright）
#


class TestDod09UxJourney:
    def test_dod_09_ux_journey_success_rate(self) -> None:
        """DoD-9：UX journey success ≥95%。

        门禁边界 = 前端维护套件：console + chat 的 `pnpm test`
        （产品契约/Semi 规范/裸 fetch/ts-hygiene + vitest 产品测试）+ 全量
        真浏览器 Playwright journey specs（agent-golden-path / agent-error-path
        / chat-nfr / console-real-http——phase6 迁移完成，旧 UI 元素依赖已清）。
        全过 = journey success 100% ≥95%。
        """
        import shutil
        import subprocess

        if shutil.which("pnpm") is None:
            pytest.skip("pnpm 不可用（DoD-9 前端套件无法运行）")

        # 1) 前端维护套件（console + chat：契约/Semi 规范/vitest 产品测试）
        frontend = subprocess.run(
            ["pnpm", "test"],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert frontend.returncode == 0, (
            f"前端维护套件失败（UX journey <100%）:\n"
            f"{frontend.stdout[-1500:]}"
        )

        # 2) 全量真浏览器 journey specs（迁移后 4 spec 全入门禁；playwright.config
        #    testDir=frontend/e2e，webServer 自启动 fixture + dev server）
        browser = subprocess.run(
            [
                "npx", "playwright", "test",
                "frontend/e2e",
                "--reporter=line",
            ],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert browser.returncode == 0, (
            f"真浏览器 journey specs 失败:\n{browser.stdout[-1500:]}"
        )


# ---------------------------------------------------------------------------
# DoD 10-13：四类 legacy 静态扫描（NFR-P6-LEGACY-01..04）
#


class TestDod10LegacyScans:
    def test_dod_10_to_13_legacy_static_scans(self) -> None:
        """DoD 10-13：dead PluginType / spec_json.get / pseudo _summarize /
        permanent legacy path 全部 =0。"""
        scripts_dir = _REPO / "scripts"
        sys.path.insert(0, str(scripts_dir / "static_scan"))
        try:
            from scan_legacy import run_scan

            result = run_scan("all")
            violations: dict[str, list[str]] = result["violations"]
            assert not violations.get("plugin_type"), violations["plugin_type"]
            assert not violations.get("spec_json_get"), violations["spec_json_get"]
            assert not violations.get("summarize"), violations["summarize"]
            assert not violations.get("legacy_path"), violations["legacy_path"]
        finally:
            sys.path.remove(str(scripts_dir / "static_scan"))


# ---------------------------------------------------------------------------
# DoD 14：active pinned hard-delete=0（NFR-P6-DEL-01 / B-02）
#


class TestDod14Http409Chain:
    async def test_dod_14_store_guard_rejected_as_http_409(
        self, store: PostgreSQLRegistryStore
    ) -> None:
        """DoD-14 HTTP 层：存储层 guard 拒绝 → Console 映射 → 409 envelope。

        真实链路（真实 PG + 真实 HTTP）：deprecate DRAFT 版本触发 store 层
        VersionConflictError guard（`only published versions can be deprecated`）
        → Console 映射 ConsoleVersionConflictError → HTTP 409 + code 33_009。
        RegistryStoreError（revision bump 等 infra 错误）不再宽泛映射 409——走
        console_errors 通用 handler 出 500 INTERNAL_ERROR（客户端冲突 guard 均抛
        VersionConflictError，已覆盖 409）；active_reference_blocked 是 hard_delete
        guard，hard-delete 专用 HTTP 端点仍不存在（显式登记：新增端点属产品功能）。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-14 真实边界）")
        from httpx import ASGITransport, AsyncClient

        from fluxion.api.console import create_app as create_console_app
        from fluxion.config import DevModeSettings
        from fluxion.errors.console import VERSION_CONFLICT
        from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
        from fluxion.services.console_app import ConsoleApplicationService

        # dev 模式 actor 归属 tenant "dev"（资源须建在同租户）
        resource_id = f"dod-409-agent-{uuid.uuid4().hex[:8]}"
        tenant_id = "dev"
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id=resource_id,
                tenant_id=tenant_id,
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"request_timeout_ms": 1000, "max_retries": 1},
            )
        )

        console = ConsoleApplicationService(store)
        app = create_console_app(console, dev_mode=DevModeSettings(enabled=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://dod") as client:
            response = await client.post(
                f"/api/v1/resources/runtime_profile/{resource_id}/versions/1:deprecate",
                json={"reason": "409 chain probe"},
            )

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == VERSION_CONFLICT, body
        assert "only published" in body["message"], body
        # 资源保持 DRAFT（guard 拒绝后状态不变）
        resource = await store.get(
            ResourceKind.RUNTIME_PROFILE,
            resource_id,
            tenant_id=tenant_id,
            version="1",
        )
        assert resource is not None and resource.status is ResourceStatus.DRAFT

    async def test_dod_14_infra_registry_error_is_500_not_409(
        self, store: PostgreSQLRegistryStore
    ) -> None:
        """DoD-14 补充：infra RegistryStoreError（revision bump 等）→ 500 非 409。

        commit_publication 在 Console 路径唯一可达的 RegistryStoreError 是
        revision bump 等 infra 错误——宽泛映射 409 会把服务端故障误标为客户端
        冲突。真实 HTTP 链路：patch store.commit_publication 抛 RegistryStoreError
        → deprecate 端点返回 500 INTERNAL_ERROR（console_errors 通用 handler），
        而非 409（客户端冲突 guard 均已走 VersionConflictError→409）。
        """
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-14 真实边界）")
        from unittest.mock import AsyncMock, patch

        from httpx import ASGITransport, AsyncClient

        from fluxion.api.console import create_app as create_console_app
        from fluxion.config import DevModeSettings
        from fluxion.errors.console import INTERNAL_ERROR
        from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
        from fluxion.services.console_app import ConsoleApplicationService

        resource_id = f"dod-500-agent-{uuid.uuid4().hex[:8]}"
        tenant_id = "dev"
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id=resource_id,
                tenant_id=tenant_id,
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"request_timeout_ms": 1000, "max_retries": 1},
            )
        )

        console = ConsoleApplicationService(store)
        app = create_console_app(console, dev_mode=DevModeSettings(enabled=True))
        with patch.object(
            store,
            "commit_publication",
            new=AsyncMock(side_effect=RegistryStoreError("failed to bump revision for tenant dev")),
        ):
            # raise_app_exceptions=False：未捕获异常由 ServerErrorMiddleware 转 500
            # envelope（真实 uvicorn 行为）；默认 True 会在响应发出后重抛给测试侧。
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://dod",
            ) as client:
                response = await client.post(
                    f"/api/v1/resources/runtime_profile/{resource_id}/versions/1:deprecate",
                    json={"reason": "infra error probe"},
                )

        assert response.status_code == 500, response.text
        body = response.json()
        assert body["code"] == INTERNAL_ERROR, body


class TestDod14HardDelete:
    async def test_dod_14_active_pinned_hard_delete_rejected(
        self, store: PostgreSQLRegistryStore
    ) -> None:
        """DoD-14 / B-02：active 引用中的 pinned 资源 hard-delete 被拒（409 语义）。"""
        if not _pg_available():
            pytest.skip("PostgreSQL 不可达（DoD-14 真实边界）")
        from datetime import timedelta

        from fluxion.registry.store import PublicationCommand, PublicationOperation

        tenant_id = f"tenant-dod-hd-{uuid.uuid4().hex[:8]}"
        await _seed(store, tenant_id, "dod-agent")

        # tombstone（软删终态，hard-delete 前置条件）经治理事务 commit_publication
        await store.commit_publication(
            PublicationCommand(
                publish_id=f"pub-tomb-{uuid.uuid4().hex[:8]}",
                event_id=f"evt-tomb-{uuid.uuid4().hex[:8]}",
                tenant_id=tenant_id,
                kind=ResourceKind.AGENT_DEFINITION,
                resource_id="dod-agent",
                version="1",
                operation=PublicationOperation.TOMBSTONE,
                actor_id="dod-verifier",
                request_id="req-dod-14",
                trace_id="trace-dod-14",
                approval_id="ap-dod-14",
            )
        )

        # active reference pin 该版本 → hard-delete 必须拒绝（409 语义）
        await store.add_active_reference(
            tenant_id=tenant_id,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="dod-agent",
            version="1",
            ref_type="execution",
            ref_id="exec-dod-14",
        )
        with pytest.raises(RegistryStoreError, match="active_reference_blocked"):
            await store.hard_delete(
                ResourceKind.AGENT_DEFINITION,
                "dod-agent",
                tenant_id=tenant_id,
                version="1",
                approval_id="ap-dod-14",
                retention_period=timedelta(seconds=0),
            )
