"""Profile Schema 校验与兼容读取（TASK-009 / ADR-A013）验收测试。

覆盖 S-CFG-01 / B-CFG-01：
- 真实边界：真实 Console Schema/Resource 服务 → PG Registry；
  PG 历史 Published → 实际 Profile 解析器。
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


def _legacy_profile_spec(**overrides: object) -> dict[str, object]:
    """历史形状：无 schema_version（v1 隐式）+ 全部 7 字段。"""
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
async def test_S_CFG_01_invalid_profile_rejected_with_field_location() -> None:
    """S-CFG-01：新版本拒绝无效配置；错误定位字段。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="bad-profile",
            version="1",
            spec=_legacy_profile_spec(max_rounds=999, bogus_field=1),
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
async def test_S_CFG_01_unknown_schema_version_rejected_explicitly() -> None:
    """S-CFG-01：未定义版本拒绝，且诊断明确指向版本（非泛型字段错误）。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="v9-profile",
            version="1",
            spec=_legacy_profile_spec(schema_version="v9"),
        )
        resp = await stack.client.post(
            "/api/v1/resources/runtime_profile/v9-profile/versions/1:validate-publish",
            headers=tenant_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["valid"] is False
    issues = "；".join(data["issues"])
    assert "v9" in issues or "schema_version" in issues


@pytest.mark.asyncio
async def test_B_CFG_01_legacy_published_readable_without_rewrite() -> None:
    """B-CFG-01：旧版本可读可解析；存储 JSON 未改写；tenant/version scope 保留。"""
    from fluxion.resources.resource_specs import read_published_profile

    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="legacy-profile",
            version="1",
            spec=_legacy_profile_spec(),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="legacy-profile",
            version="1",
        )
        assert published.status_code == 200
        row = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "legacy-profile",
            tenant_id="tenant-a",
            version="1",
        )
        assert row is not None
        stored_before = dict(row.spec_json)
        profile = read_published_profile(row.spec_json)
        assert profile.schema_version == "v1"
        assert profile.max_rounds == 8
        # 兼容读不改写存储；输入 dict 不被 mutation。
        assert dict(row.spec_json) == stored_before
        assert "schema_version" not in stored_before
        # 跨 tenant 不可读。
        other = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "legacy-profile",
            tenant_id="tenant-b",
            version="1",
        )
        assert other is None
