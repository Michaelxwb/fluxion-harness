"""TASK-019：ConnectionTestService 连接测试。

- B-S-07：配置 Provider/凭据 → 测试连接返回可达性 + 发现模型；MCP 握手发现工具。
- B-E-04：凭据/端点错误 → 返回可操作错误（不静默失败）；凭据解析失败同样可操作。

httpx.MockTransport 模拟真实 HTTP；真实边界 = 服务经 HTTP 探测 + ProviderDefinition 解析。
"""

from __future__ import annotations

import httpx


async def _async_value(value: str) -> str:
    return value
import sys
from pathlib import Path

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.secrets import SecretProviderError
from fluxion.services.connection_test import ConnectionTestService


async def _put_provider(
    store: SQLiteRegistryStore,
    provider_id: str,
    *,
    base_url: str = "https://api.deepseek.com",
) -> None:
    await store.put(
        ResourceDefinition(
            kind=ResourceKind.MODEL_PROVIDER,
            id=provider_id,
            tenant_id="tenant-a",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "protocol": "openai-compatible",
                "base_url": base_url,
                "credential_ref": "secret://tenant-a/openai",
                "default_model": "deepseek-chat",
                "request_timeout_ms": 10_000,
                "max_retries": 1,
            },
        )
    )


def _client_factory(handler: httpx.MockTransport) -> object:
    return lambda: httpx.AsyncClient(transport=handler, timeout=5.0)


@pytest.mark.asyncio
async def test_B_S07_connection_reachable_discovers_models() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_provider(store, "prov-deepseek")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]},
            )
        )
        service = ConnectionTestService(
            store,
            client_factory=_client_factory(transport),  # type: ignore[arg-type]
            api_key_provider=lambda _ref: _async_value("sk-test"),
        )
        result = await service.test_connection(tenant_id="tenant-a", provider_id="prov-deepseek")
        assert result.reachable is True
        assert result.discovered_models == ["deepseek-chat", "deepseek-reasoner"]
        assert result.error is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_B_E04_connection_error_actionable() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_provider(store, "prov-bad")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "unauthorized"})
        )
        service = ConnectionTestService(
            store,
            client_factory=_client_factory(transport),  # type: ignore[arg-type]
            api_key_provider=lambda _ref: _async_value("sk-wrong"),
        )
        result = await service.test_connection(tenant_id="tenant-a", provider_id="prov-bad")
        assert result.reachable is False
        assert result.error is not None
        assert "401" in result.error
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_B_E04_credential_resolution_failure_actionable() -> None:
    """TASK-019 返工：凭据解析失败（Secret 缺失/装配缺失）→ 可操作错误，
    不发无 Authorization 的静默请求。"""

    async def _broken_provider(_ref: str) -> str | None:
        raise SecretProviderError("secret_not_found", "secret://tenant-a/openai not found")

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_provider(store, "prov-no-secret")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        )
        service = ConnectionTestService(
            store,
            client_factory=_client_factory(transport),  # type: ignore[arg-type]
            api_key_provider=_broken_provider,
        )
        result = await service.test_connection(
            tenant_id="tenant-a", provider_id="prov-no-secret"
        )
        assert result.reachable is False
        assert result.error is not None
        assert "凭据解析失败" in result.error
    finally:
        await store.close()


async def _put_mcp_stdio(store: SQLiteRegistryStore, mcp_id: str) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_product_server.py"
    await store.put(
        ResourceDefinition(
            kind=ResourceKind.MCP,
            id=mcp_id,
            tenant_id="tenant-a",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "name": "weather",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(fixture)],
                "env": {},
                "timeout_ms": 5_000,
                "allowed_tools": [],
            },
        )
    )


@pytest.mark.asyncio
async def test_B_S07_mcp_stdio_connection_discovers_tools() -> None:
    """B-S-07：MCP 连接测试经真实 stdio 握手发现工具（进程边界真实，非 mock）。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await _put_mcp_stdio(store, "weather")
        service = ConnectionTestService(store)
        result = await service.test_mcp_connection(
            tenant_id="tenant-a", mcp_id="weather"
        )
        assert result.reachable is True
        assert result.discovered_tools == ["lookup"]
        assert result.error is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_B_E04_mcp_missing_actionable_error() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        service = ConnectionTestService(store)
        result = await service.test_mcp_connection(
            tenant_id="tenant-a", mcp_id="missing-mcp"
        )
        assert result.reachable is False
        assert result.error is not None
        assert "missing-mcp" in result.error
    finally:
        await store.close()
