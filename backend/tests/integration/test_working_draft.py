"""REQ-EXE-004 / remediation §14.3·§25（TASK-008）：published immutable + working draft 自动维护。

B-S-05：已发布 v3 编辑 → 自动创建/复用 working draft，v3 不变；发布 → v4。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind, ResourceStatus
from fluxion.services.console_contracts import (
    ConsoleActor,
    CreateResourceDraftRequest,
    PublishResourceVersionRequest,
    UpdateResourceDraftRequest,
)
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers


def _actor() -> ConsoleActor:
    return ConsoleActor(
        tenant_id="tenant-a", actor_id="admin-a", request_id="req", trace_id="trace"
    )


def _profile_spec(timeout_ms: int) -> dict[str, object]:
    return {
        "request_timeout_ms": timeout_ms,
        "max_retries": 1,
        "max_rounds": 8,
        "concurrency": 1,
        "memory_budget_mb": 512,
    }


@pytest.mark.asyncio
async def test_B_S05_working_draft_forks_published_and_keeps_it_immutable() -> None:
    async with console_stack() as stack:
        svc = stack.service
        actor = _actor()
        # 建立 published v1/v2/v3
        for i in range(1, 4):
            v = str(i)
            await svc.create_resource_draft(
                actor,
                CreateResourceDraftRequest(
                    tenant_id="tenant-a",
                    kind=ResourceKind.RUNTIME_PROFILE,
                    resource_id="asst",
                    version=v,
                    spec=_profile_spec(1000 + i),
                ),
            )
            await svc.publish_resource_version(
                actor,
                PublishResourceVersionRequest(
                    tenant_id="tenant-a",
                    kind=ResourceKind.RUNTIME_PROFILE,
                    resource_id="asst",
                    version=v,
                    expected_base_version=None if v == "1" else str(i - 1),
                ),
            )
        v3 = await svc.get_resource(actor, ResourceKind.RUNTIME_PROFILE, "asst", version="3")
        assert v3.status is ResourceStatus.PUBLISHED
        v3_spec = dict(v3.spec_json)

        # 编辑已发布 → 自动 fork working draft（v4）
        draft1 = await svc.ensure_working_draft(
            actor, ResourceKind.RUNTIME_PROFILE, "asst"
        )
        assert draft1.status is ResourceStatus.DRAFT
        assert draft1.version == "4"
        assert draft1.spec_json == v3_spec  # fork 自 v3

        # 复用：连续调用返回同一 working draft，不重复 fork
        draft2 = await svc.ensure_working_draft(
            actor, ResourceKind.RUNTIME_PROFILE, "asst"
        )
        assert draft2.version == draft1.version

        # v3 不可变（fork 后仍未改）
        v3_after = await svc.get_resource(
            actor, ResourceKind.RUNTIME_PROFILE, "asst", version="3"
        )
        assert v3_after.status is ResourceStatus.PUBLISHED
        assert v3_after.spec_json == v3_spec

        # 编辑 working draft + 发布 → v4
        await svc.update_resource_draft(
            actor,
            UpdateResourceDraftRequest(
                tenant_id="tenant-a",
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="asst",
                version="4",
                spec=_profile_spec(9999),
            ),
        )
        await svc.publish_resource_version(
            actor,
            PublishResourceVersionRequest(
                tenant_id="tenant-a",
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="asst",
                version="4",
                expected_base_version="3",
            ),
        )
        v4 = await svc.get_resource(actor, ResourceKind.RUNTIME_PROFILE, "asst", version="4")
        assert v4.status is ResourceStatus.PUBLISHED
        assert v4.spec_json["request_timeout_ms"] == 9999
        # v3 仍不可变
        v3_final = await svc.get_resource(
            actor, ResourceKind.RUNTIME_PROFILE, "asst", version="3"
        )
        assert v3_final.spec_json == v3_spec


@pytest.mark.asyncio
async def test_B_S05_working_draft_endpoint_returns_draft() -> None:
    """POST :working-draft 端点：已发布资源 → 返回 working draft（复用）。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client, kind=ResourceKind.RUNTIME_PROFILE, resource_id="asst", version="1",
            spec=_profile_spec(1000),
        )
        await publish_resource(
            stack.client, kind=ResourceKind.RUNTIME_PROFILE, resource_id="asst", version="1",
            expected_base_version=None,
        )
        first = await stack.client.post(
            "/api/v1/resources/runtime_profile/asst:working-draft",
            headers=tenant_headers(),
        )
        second = await stack.client.post(
            "/api/v1/resources/runtime_profile/asst:working-draft",
            headers=tenant_headers(),
        )
    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["version"] == "2"
    assert first_data["status"] == "draft"
    assert second_data["version"] == first_data["version"]  # 复用不重复 fork
