"""Profile Schema V2 校验（105 P1-01 / 方案 A）验收测试。

覆盖 S-02（端到端层）：
- 真实边界：真实 Console Schema/Resource 服务 → PG Registry。
- 无 v1 兼容：v1 形状一律拒绝；DB 删除重建，无迁移。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    tenant_headers,
)


def _v2_profile_spec(**overrides: object) -> dict[str, object]:
    """V2 形状：仅 max_rounds/default/bootstrapped_from。"""
    spec: dict[str, object] = {
        "max_rounds": 8,
        "bootstrapped_from": None,
        "default": True,
    }
    spec.update(overrides)
    return spec


def _v1_profile_spec(**overrides: object) -> dict[str, object]:
    """v1 形状（含已删字段）：V2 下必须拒绝，无兼容。"""
    spec: dict[str, object] = {
        "request_timeout_ms": 30_000,
        "max_retries": 1,
        "max_rounds": 8,
        "concurrency": 1,
        "memory_budget_mb": 512,
        "bootstrapped_from": None,
        "default": True,
    }
    spec.update(overrides)
    return spec


@pytest.mark.asyncio
async def test_S_02_invalid_profile_rejected_with_field_location() -> None:
    """S-02：无效配置拒绝；错误定位字段。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="bad-profile",
            version="1",
            spec=_v2_profile_spec(max_rounds=999, bogus_field=1),
        )
        resp = await stack.client.post(
            "/api/v1/resources/runtime_profile/bad-profile/versions/1:validate-publish",
            headers=tenant_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["valid"] is False
    issues = "；".join(data["issues"])
    assert "max_rounds" in issues


@pytest.mark.asyncio
async def test_S_02_v1_shape_rejected_without_compat() -> None:
    """S-02：v1 形状（含已删字段）拒绝，无兼容桥；发布被阻断。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="v1-profile",
            version="1",
            spec=_v1_profile_spec(),
        )
        resp = await stack.client.post(
            "/api/v1/resources/runtime_profile/v1-profile/versions/1:validate-publish",
            headers=tenant_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is False
        issues = "；".join(data["issues"])
        assert "request_timeout_ms" in issues
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="v1-profile",
            version="1",
        )
        assert published.status_code != 200


@pytest.mark.asyncio
async def test_S_02_valid_v2_publishable() -> None:
    """S-02：合法 v2 可发布；tenant scope 保留；跨 tenant 不可读。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="v2-profile",
            version="1",
            spec=_v2_profile_spec(),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="v2-profile",
            version="1",
        )
        assert published.status_code == 200
        row = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "v2-profile",
            tenant_id="tenant-a",
            version="1",
        )
        assert row is not None
        assert set(row.spec_json) == {"max_rounds", "bootstrapped_from", "default"}
        other = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "v2-profile",
            tenant_id="tenant-b",
            version="1",
        )
        assert other is None
