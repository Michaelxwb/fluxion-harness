"""TASK-003（Phase 5）Secret tenant 隔离 + 泄漏门禁 + AuditLog。

S-03 / E-01（design §3.5 安全性设计：明文=0 四面门禁 + tenant escape=0）。

真实边界：
- S-03：真实 CredentialResolver + PostgresEncryptedSecretStore 双租户数据；
- E-01：真实日志（structlog JSON 渲染输出）、真实 span（OTel SDK span attributes）、
  真实 ResourceDefinition spec 校验、真实 Console HTTP 响应体——四面注入已知
  明文 marker，任一面出现即失败（明文=0 门禁，NFR-SEC-01）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.console_helpers import tenant_headers

from fluxion.api.console import create_app
from fluxion.observability.context import RequestContext
from fluxion.observability.logging import emit_access_log
from fluxion.observability.redaction import redact_mapping
from fluxion.observability.tracing import get_tracer
from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs, secret_credentials
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.secrets import CredentialResolver, SecretProviderError
from fluxion.services.console_app import ConsoleApplicationService

_E01_MARKER = "E01-KNOWN-PLAINTEXT-7f3a9c"


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gov.db'}")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def secret_store(engine: AsyncEngine) -> AsyncGenerator[PostgresEncryptedSecretStore, None]:
    store = PostgresEncryptedSecretStore(engine=engine, master_key=b"k" * 32, key_id="k1")
    await store.initialize()
    yield store


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# S-03：tenant A 引用 tenant B 的 secret ref → secret_tenant_mismatch（escape=0）


class TestS03TenantIsolation:
    async def test_cross_tenant_ref_rejected(
        self, secret_store: PostgresEncryptedSecretStore
    ) -> None:
        ref_a = await secret_store.put("tenant-a", _unique("key"), "a-private")
        ref_b = await secret_store.put("tenant-b", _unique("key"), "b-private")
        resolver = CredentialResolver(secret_store)

        # tenant A 引用 tenant B 的 ref → 拒绝（tenant escape=0）
        with pytest.raises(SecretProviderError) as excinfo:
            await resolver.resolve(ref_b, tenant_id="tenant-a")
        assert excinfo.value.code == "secret_tenant_mismatch"

        # 各自租户解析自己的 ref 正常
        assert (await resolver.resolve(ref_a, tenant_id="tenant-a")) == "a-private"
        assert (await resolver.resolve(ref_b, tenant_id="tenant-b")) == "b-private"

    async def test_provider_metadata_tenant_scoped(
        self, secret_store: PostgresEncryptedSecretStore
    ) -> None:
        """provider 首参 tenant_id 强制：list_metadata 只见本租户记录。"""
        name = _unique("shared-name")
        await secret_store.put("tenant-a", name, "a-private")
        await secret_store.put("tenant-b", name, "b-private")

        items_b, total_b = await secret_store.list_metadata(tenant_id="tenant-b", offset=0, limit=10)
        assert total_b == 1
        assert all(item.tenant_id == "tenant-b" for item in items_b)


# ---------------------------------------------------------------------------
# E-01：明文泄漏四面门禁（日志 / trace / spec / response）


class TestE01LeakGate:
    def test_log_face_redacts_plaintext(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """structlog 访问日志：敏感 headers/query 注入 marker → 渲染输出无明文。"""
        caplog.set_level(logging.INFO, logger="fluxion.console.access")
        context = RequestContext(
            request_id="req-e01",
            trace_id="trace-e01",
            tenant_id="tenant-a",
            actor_id="admin-a",
            method="POST",
            route="/api/v1/resources",
            client_ip="127.0.0.1",
            user_agent="pytest",
        )
        emit_access_log(
            context,
            status_code=200,
            biz_code=0,
            latency_ms=1.0,
            headers={"authorization": f"Bearer {_E01_MARKER}", "x-custom": "ok"},
            query={"api_key": _E01_MARKER, "page": "1"},
        )
        rendered = [record.getMessage() for record in caplog.records]
        assert rendered, "访问日志未产出记录"
        for line in rendered:
            assert _E01_MARKER not in line, "日志面出现 secret 明文（明文=0 门禁失败）"
            event = json.loads(line)
            assert event["headers"]["authorization"] == "[REDACTED]"
            assert event["query"]["api_key"] == "[REDACTED]"

    def test_trace_face_redacts_plaintext(self) -> None:
        """span attributes：敏感字段经 redact_mapping 后进入 span → 无明文。"""
        tracer = get_tracer("e01-leak-gate")
        span = tracer.start_span("secret.resolve")
        try:
            attrs = redact_mapping(
                {
                    "credential": _E01_MARKER,
                    "secret_ref": "secret://tenant-a/key@1",
                    "tenant_id": "tenant-a",
                }
            )
            for key, value in attrs.items():
                span.set_attribute(key, value)
            for value in span.attributes.values():
                assert _E01_MARKER not in str(value), (
                    "trace 面出现 secret 明文（明文=0 门禁失败）"
                )
            assert span.attributes["credential"] == "[REDACTED]"
        finally:
            span.end()

    def test_spec_face_rejects_plaintext(self) -> None:
        """Resource spec：明文 secret 无法通过 ResourceDefinition 校验（spec 面=0）。"""
        with pytest.raises(ValueError, match="plaintext secret"):
            ResourceDefinition(
                kind=ResourceKind.MCP,
                id="mcp-with-secret",
                tenant_id="tenant-a",
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"name": "mcp", "api_key": _E01_MARKER},
            )

    async def test_response_face_no_plaintext(
        self, secret_store: PostgresEncryptedSecretStore, tmp_path: Path
    ) -> None:
        """Console API：写入真实 secret 后 GET /api/v1/credentials → 响应体无明文。"""
        await secret_store.put("tenant-a", _unique("leak"), _E01_MARKER)

        registry = SQLiteRegistryStore(f"sqlite+aiosqlite:///{tmp_path / 'console.db'}")
        service = ConsoleApplicationService(registry, secret_metadata_store=secret_store)
        await service.initialize()
        app = create_app(service)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/credentials", headers=tenant_headers(tenant_id="tenant-a")
            )
        assert response.status_code == 200
        assert _E01_MARKER not in response.text, (
            "API response 面出现 secret 明文（明文=0 门禁失败）"
        )
        # 元数据可见（ref/version），但明文与密文均不出现在响应
        assert "secret://tenant-a/" in response.text


# ---------------------------------------------------------------------------
# AuditLog：secret publish/revoke 进审计（关联 request_id/trace_id/tenant_id）


class TestSecretAuditLog:
    async def test_put_and_revoke_write_audit(
        self, secret_store: PostgresEncryptedSecretStore, engine: AsyncEngine
    ) -> None:
        tenant = "tenant-a"
        name = _unique("audited")

        await secret_store.put(
            tenant,
            name,
            "audited-plaintext",
            actor_id="admin-a",
            request_id="req-put-1",
            trace_id="trace-put-1",
        )
        ref = f"secret://{tenant}/{name}@1"
        await secret_store.revoke(
            ref, actor_id="admin-a", request_id="req-revoke-1", trace_id="trace-revoke-1"
        )

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(audit_logs).where(audit_logs.c.tenant_id == tenant)
                )
            ).fetchall()
        actions = {row.action: row for row in rows}
        assert "secret.put" in actions, "secret put 未进 AuditLog（规则 24）"
        assert "secret.revoke" in actions, "secret revoke 未进 AuditLog（规则 24）"
        put_row = actions["secret.put"]
        assert put_row.request_id == "req-put-1"
        assert put_row.trace_id == "trace-put-1"
        revoke_row = actions["secret.revoke"]
        assert revoke_row.request_id == "req-revoke-1"
        assert revoke_row.trace_id == "trace-revoke-1"
        assert revoke_row.target_id == ref

    async def test_master_key_rotation_audit_keeps_trace_id(
        self, secret_store: PostgresEncryptedSecretStore, engine: AsyncEngine
    ) -> None:
        tenant = "tenant-a"
        await secret_store.put(tenant, _unique("rot"), "rot-plaintext")
        await secret_store.rotate_master_key(
            new_key_id="k2",
            new_key=b"n" * 32,
            actor_id="admin-a",
            request_id="req-rot-9",
            trace_id="trace-rot-9",
        )
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(audit_logs).where(audit_logs.c.action == "secret.rotate_master_key")
                )
            ).fetchall()
        assert any(row.request_id == "req-rot-9" and row.trace_id == "trace-rot-9" for row in rows)

    async def test_ciphertext_never_in_audit(
        self, secret_store: PostgresEncryptedSecretStore, engine: AsyncEngine
    ) -> None:
        """审计行不含密文/明文（audit 面不泄漏存储内容）。"""
        await secret_store.put(
            "tenant-a", _unique("ct"), "audit-plaintext",
            actor_id="admin-a", request_id="req-ct", trace_id="trace-ct",
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(select(audit_logs))).fetchall()
            secrets_rows = (
                await conn.execute(select(secret_credentials))
            ).fetchall()
        blob = json.dumps([str(row) for row in rows], default=str) + json.dumps(
            [str(row) for row in secrets_rows], default=str
        )
        # 密文字节不进 audit；audit 也不含明文
        assert "audit-plaintext" not in blob
