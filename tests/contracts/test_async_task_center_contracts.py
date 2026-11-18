"""TASK-004 contract: shared execution detail and task-center background projections."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from muad_contracts import (
    ExecutionDetail,
    TaskArtifactDeliveryRef,
    TaskArtifactRef,
    TaskCenterTaskItem,
    TaskChildItem,
    TaskResultCounts,
    TaskStatus,
    TaskType,
)


def test_execution_detail_accepts_immediate_schema_and_extends_background_fields() -> None:
    detail = ExecutionDetail(
        execution_id=uuid4(),
        agent_id=uuid4(),
        task_type=TaskType.BATCH,
        skill_name="客户对账",
        status=TaskStatus.COMPLETED,
        counts=TaskResultCounts(total=5, succeeded=3, failed=2),
        partial_failure=True,
        artifact_total=1,
        snapshot_schema_version=3,
        snapshot_bytes=0,
    )
    assert detail.snapshot_schema_version == 3
    assert detail.counts.failed == 2
    assert detail.children.total == 0


def test_task_center_item_and_child_item_parse_wire_shape() -> None:
    item = TaskCenterTaskItem(
        task_id=uuid4(),
        agent_id=uuid4(),
        task_type="BATCH",
        skill_name="客户对账",
        status="COMPLETED",
        counts={"total": 5, "succeeded": 3, "failed": 2, "cancelled": 0},
        partial_failure=True,
        child_total=5,
        child_finished=5,
        has_artifacts=True,
        create_time=datetime.now(UTC),
    )
    child = TaskChildItem(
        task_id=uuid4(),
        item_key="customer-c",
        label="客户 C",
        status=TaskStatus.FAILED,
        error_code="HTTP_401",
        error_message_summary="HTTP 401 Unauthorized",
        artifact_count=0,
    )
    assert item.task_type is TaskType.BATCH
    assert item.counts.failed == 2
    assert child.label == "客户 C"


def test_artifact_ref_carries_delivery_statuses_without_breaking_legacy_shape() -> None:
    artifact = TaskArtifactRef(
        artifact_id=uuid4(),
        name="result.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=1024,
        checksum="sha256:" + "a" * 64,
        version="2",
        deliveries=[
            TaskArtifactDeliveryRef(target_key="webchat:conv", status="DELIVERED"),
            TaskArtifactDeliveryRef(
                target_key="wecom:user", status="DEGRADED", fallback_url="https://example.test/f"
            ),
        ],
    )
    assert artifact.deliveries[1].status == "DEGRADED"
