"""ADR-A008 Model 领域三层 · Registry 版本化契约（TASK-001）。

验证新增 `MODEL_PROVIDER` / `MODEL_DEFINITION` 两个一等 kind 可经
Registry（SQLite）版本化发布并取回，spec 形状与 ProviderDefinition /
ModelDefinition typed spec 一致。Store 为 kind 无关（spec_json 通用存储），
本测试证明新 kind 在版本化链路开箱可用。
"""

from __future__ import annotations

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)


@pytest.mark.asyncio
async def test_A008_model_kinds_versioned_roundtrip() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.MODEL_PROVIDER,
                id="prov-deepseek",
                tenant_id="tenant-a",
                version="v1",
                status=ResourceStatus.DRAFT,
                spec_json={
                    "protocol": "openai-compatible",
                    "base_url": "https://api.deepseek.com",
                    "credential_ref": "secret://tenant-a/openai",
                    "default_model": "deepseek-chat",
                    "request_timeout_ms": 60_000,
                    "max_retries": 1,
                },
            )
        )
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.MODEL_DEFINITION,
                id="deepseek-chat",
                tenant_id="tenant-a",
                version="v1",
                status=ResourceStatus.DRAFT,
                spec_json={
                    "name": "deepseek-chat",
                    "provider_ref": {"id": "prov-deepseek", "version": "v1"},
                    "capabilities": {"context_window": 65536, "tool_calling": True},
                },
            )
        )

        provider = await store.get(
            ResourceKind.MODEL_PROVIDER,
            "prov-deepseek",
            tenant_id="tenant-a",
            version="v1",
        )
        model = await store.get(
            ResourceKind.MODEL_DEFINITION,
            "deepseek-chat",
            tenant_id="tenant-a",
            version="v1",
        )

        assert provider is not None
        assert provider.kind is ResourceKind.MODEL_PROVIDER
        assert provider.spec_json["base_url"] == "https://api.deepseek.com"
        assert model is not None
        assert model.kind is ResourceKind.MODEL_DEFINITION
        assert model.spec_json["provider_ref"]["version"] == "v1"
    finally:
        await store.close()
