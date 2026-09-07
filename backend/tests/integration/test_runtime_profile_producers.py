"""后端 Profile 配置生产入口（TASK-010 / ADR-A013）验收测试。

覆盖 S-CFG-02：
- 真实边界：CLI/bootstrap/create/import → Profile 服务 → PG；
- 每个新配置入口遵循版本契约；不再注入无效字段；旧导入按 ADR 处理。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from fluxion.services.runtime_contracts import (
    CreateRuntimeProfileRequest,
    default_runtime_profile_request,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN


def test_S_CFG_02_bootstrap_factory_does_not_inject_unwired_fields() -> None:
    """S-CFG-02：bootstrap 默认工厂不再隐式注入未接入字段值。"""
    request = default_runtime_profile_request(
        tenant_id="tenant-a", runtime_profile_id="assistant"
    )
    # request_timeout_ms 未接入执行（ADR-A013）；工厂不得再覆盖非默认值造成
    # "配置了但不生效"的虚假承诺——保持 dataclass 默认。
    assert request.request_timeout_ms == 60_000
    assert request.default is True


def test_S_CFG_02_profile_spec_builder_stamps_version_strict() -> None:
    """S-CFG-02：配置构造走严格校验 + 显式版本戳；非法值报类型化错误。"""
    from fluxion.resources.resource_specs import ProfileSchemaError
    from fluxion.services.runtime_utils import _runtime_profile_spec

    request = CreateRuntimeProfileRequest(
        tenant_id="tenant-a",
        runtime_profile_id="assistant",
        version="1",
        max_rounds=8,
    )
    spec = _runtime_profile_spec(request)
    assert spec["schema_version"] == "v1"
    assert spec["max_rounds"] == 8

    bad = CreateRuntimeProfileRequest(
        tenant_id="tenant-a",
        runtime_profile_id="assistant",
        version="1",
        max_rounds=999,
    )
    with pytest.raises(ProfileSchemaError, match="runtime_profile_schema_invalid"):
        _runtime_profile_spec(bad)


@pytest.mark.asyncio
async def test_S_CFG_02_platform_default_and_create_are_versioned() -> None:
    """S-CFG-02：platform-default 自举与 service 创建落盘均带版本；旧导入兼容。"""
    from fluxion.registry import PostgreSQLRegistryStore
    from fluxion.resources.resource_specs import read_published_profile
    from fluxion.services.runtime_app import RuntimeApplicationService

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        service = RuntimeApplicationService.create_dev_bundle(store)
        await service.initialize()
        try:
            await service.ensure_runtime_profile(
                default_runtime_profile_request(
                    tenant_id="tenant-a", runtime_profile_id="assistant"
                )
            )
            row = await store.get(
                ResourceKind.RUNTIME_PROFILE,
                "assistant",
                tenant_id="tenant-a",
                version="1",
            )
            assert row is not None
            assert row.spec_json["schema_version"] == "v1"
            # 旧导入形状（无版本）按 ADR-A013 兼容为 v1。
            legacy = dict(row.spec_json)
            del legacy["schema_version"]
            assert read_published_profile(legacy).schema_version == "v1"
        finally:
            await service.close()
    finally:
        await store.close()
