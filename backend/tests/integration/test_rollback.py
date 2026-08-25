from __future__ import annotations

from tests.console_helpers import (
    console_stack,
    create_resource,
    deprecate_resource,
    publish_resource,
    rollback_resource,
    tenant_headers,
)

from fluxion.resources import ResourceKind


async def test_E_C107_deprecated_rollback_requires_force_approval() -> None:
    async with console_stack() as stack:
        for version in ("1", "2"):
            await create_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
            )
            await publish_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
                expected_base_version="1",
            )
        deprecated = await deprecate_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
        )
        blocked = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
        )
        # 伪造 approval_id 必须被拒绝（403），而不是被当作审批通过。
        fabricated = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
            force=True,
            approval_id="approval-fabricated",
            actor_id="requester-a",
            request_id="req-E-C107-fabricated",
        )
        # 真实审批流：requester 发起审批，approver 决策通过，requester 执行回滚。
        created = await stack.client.post(
            "/api/v1/approvals",
            json={
                "resource_type": "runtime_profile",
                "resource_id": "assistant",
                "target_version": "1",
                "reason": "rollback to stable",
            },
            headers=tenant_headers(
                actor_id="requester-a",
                request_id="req-E-C107-approval-create",
            ),
        )
        approval_id = created.json()["data"]["approval_id"]
        decided = await stack.client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"approved": True, "reason": "approved"},
            headers=tenant_headers(
                actor_id="approver-a",
                request_id="req-E-C107-approval-decide",
            ),
        )
        approved = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
            force=True,
            approval_id=approval_id,
            actor_id="requester-a",
            request_id="req-E-C107-approved",
        )
        active = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            tenant_id="tenant-a",
        )

    assert deprecated.status_code == 200
    assert blocked.status_code == 409
    assert "审批" in blocked.json()["message"]
    assert fabricated.status_code == 403
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "pending"
    assert decided.status_code == 200
    assert decided.json()["data"]["status"] == "approved"
    assert approved.status_code == 200
    assert approved.json()["data"]["version"] == "1"
    assert approved.json()["data"]["event_status"] == "pending"
    assert active is not None
    assert active.version == "1"


async def test_E_C108_rollback_approval_consumed_once_rejects_replay() -> None:
    """A9：回滚审批单一次性消费——同一 approval_id 不可用于第二次回滚。

    回滚到 DEPRECATED 目标会把目标改回 PUBLISHED（_next_status），故审批要求不
    持久。本测试在首次回滚后**重新 deprecate v1** 以恢复审批要求，再用同一
    approval_id 重放第二次回滚——必须 403（已消费）。这正是不带消费语义时的
    绕过路径：单次审批永久复用。
    """
    async with console_stack() as stack:
        for version in ("1", "2"):
            await create_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
            )
            await publish_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
                expected_base_version="1",
            )
        await deprecate_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
        )
        created = await stack.client.post(
            "/api/v1/approvals",
            json={
                "resource_type": "runtime_profile",
                "resource_id": "assistant",
                "target_version": "1",
                "reason": "rollback to stable",
            },
            headers=tenant_headers(
                actor_id="requester-a",
                request_id="req-E-C108-approval-create",
            ),
        )
        approval_id = created.json()["data"]["approval_id"]
        await stack.client.post(
            f"/api/v1/approvals/{approval_id}:decide",
            json={"approved": True, "reason": "approved"},
            headers=tenant_headers(
                actor_id="approver-a",
                request_id="req-E-C108-approval-decide",
            ),
        )
        # 第一次回滚 v2→v1：消费审批单，v1 由 DEPRECATED 转回 PUBLISHED（active=v1）。
        first = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
            force=True,
            approval_id=approval_id,
            actor_id="requester-a",
            request_id="req-E-C108-first",
        )
        # 重新 deprecate v1：恢复回滚到 v1 的审批要求（v1→DEPRECATED，active 回落 v2）。
        await deprecate_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
        )
        # 第二次回滚 v2→v1 复用同一审批单 → 必须 403（已消费，不可重放）。
        replay = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
            force=True,
            approval_id=approval_id,
            actor_id="requester-a",
            request_id="req-E-C108-replay",
        )

    assert first.status_code == 200
    assert replay.status_code == 403
    assert "已消费" in replay.json()["message"]
