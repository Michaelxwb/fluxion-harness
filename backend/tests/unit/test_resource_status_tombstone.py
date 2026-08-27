"""ADR-SNAPSHOT-001 TASK-002：ResourceStatus TOMBSTONE 状态机（B-02）。

真实边界（契约声明）：`publish_sqlalchemy._next_status` 真实校验逻辑（非 mock），
覆盖新增 TOMBSTONE 分支与既有迁移的回归。

RED 约定（cf-task:start #7）：`PublicationOperation.TOMBSTONE` 未实现 →
AttributeError/ValueError，即真实 RED。
"""

from __future__ import annotations

import pytest

from fluxion.registry.publish_sqlalchemy import _next_status
from fluxion.registry.store import PublicationCommand, PublicationOperation, VersionConflictError
from fluxion.resources import ResourceKind, ResourceStatus


def _command(operation: PublicationOperation, *, approval_id: str | None = None) -> PublicationCommand:
    return PublicationCommand(
        publish_id="pub-b02",
        event_id="evt-b02",
        tenant_id="tenant-a",
        kind=ResourceKind.WORKFLOW,
        resource_id="wf-checkout",
        version="v3",
        operation=operation,
        actor_id="b02-tester",
        request_id="req-b02",
        trace_id="trace-b02",
        approval_id=approval_id,
    )


def test_b02_legal_transition_chain_reaches_tombstone() -> None:
    # DRAFT→PUBLISHED→DEPRECATED→TOMBSTONE 合法链
    assert (
        _next_status(_command(PublicationOperation.PUBLISH), ResourceStatus.DRAFT)
        is ResourceStatus.PUBLISHED
    )
    assert (
        _next_status(_command(PublicationOperation.DEPRECATE), ResourceStatus.PUBLISHED)
        is ResourceStatus.DEPRECATED
    )
    assert (
        _next_status(
            _command(PublicationOperation.TOMBSTONE, approval_id="approval-tomb"),
            ResourceStatus.DEPRECATED,
        )
        is ResourceStatus.TOMBSTONE
    )


def test_b02_published_can_tombstone_directly() -> None:
    # §3.2 状态机：PUBLISHED→TOMBSTONE（跳过 DEPRECATED）同样合法
    assert (
        _next_status(
            _command(PublicationOperation.TOMBSTONE, approval_id="approval-tomb"),
            ResourceStatus.PUBLISHED,
        )
        is ResourceStatus.TOMBSTONE
    )


def test_b02_tombstone_requires_approval() -> None:
    # REVIEW-C：tombstone 是高影响操作，与 rollback 对齐强制 approval_id——
    # 缺 approval 的 PUBLISHED/DEPRECATED→TOMBSTONE 迁移被拒。
    for current in (ResourceStatus.PUBLISHED, ResourceStatus.DEPRECATED):
        with pytest.raises(VersionConflictError, match="tombstone requires approval"):
            _next_status(
                _command(PublicationOperation.TOMBSTONE),
                current,
            )


def test_b02_rollback_from_deprecated_still_legal() -> None:
    # 既有迁移回归：DEPRECATED→PUBLISHED（rollback，需 approval）
    assert (
        _next_status(
            _command(PublicationOperation.ROLLBACK, approval_id="approval-1"),
            ResourceStatus.DEPRECATED,
        )
        is ResourceStatus.PUBLISHED
    )


def test_b02_illegal_tombstone_transitions_rejected() -> None:
    # DRAFT 不可直接 tombstone（未发布版本没有 pinned payload 语义）
    with pytest.raises(VersionConflictError):
        _next_status(_command(PublicationOperation.TOMBSTONE), ResourceStatus.DRAFT)
    # TOMBSTONE 是终态：任何操作（含重复 tombstone）都拒绝
    for operation in PublicationOperation:
        with pytest.raises(VersionConflictError):
            _next_status(_command(operation, approval_id="approval-1"), ResourceStatus.TOMBSTONE)


def test_b02_state_machine_never_reenters_draft() -> None:
    """published 后 immutable 的状态机形式化：任何合法迁移都不回到 DRAFT。

    非 DRAFT 行的 spec_json 只能经治理状态迁移被保留/软删，不存在把已发布
    版本退回可编辑 DRAFT 的路径（可编辑性即 DRAFT 态）。
    """
    for operation in PublicationOperation:
        for status in (ResourceStatus.PUBLISHED, ResourceStatus.DEPRECATED, ResourceStatus.TOMBSTONE):
            try:
                next_status = _next_status(
                    _command(operation, approval_id="approval-1"), status
                )
            except VersionConflictError:
                continue
            assert next_status is not ResourceStatus.DRAFT
