from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fluxion.resources import ResourceKind, ResourceVisibility


@dataclass(frozen=True, slots=True)
class ConsoleActor:
    tenant_id: str
    actor_id: str
    request_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class CreateResourceDraftRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    spec: dict[str, object]
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


@dataclass(frozen=True, slots=True)
class UpdateResourceDraftRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    spec: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReleaseGateRequest:
    """publish 附带的 Release Gate 参数（Phase 5 TASK-005）。"""

    candidate_eval_run_id: str
    baseline_eval_run_id: str
    threshold: float = 0.0


@dataclass(frozen=True, slots=True)
class PublishResourceVersionRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    expected_base_version: str | None = None
    publish_note: str | None = None
    # Phase 5 TASK-005：gate 参数存在且 service 配置 ReleaseGateService 时，
    # publish 前评估门禁；blocked → 阻断发布（score_delta 诊断入 envelope）。
    gate: ReleaseGateRequest | None = None


@dataclass(frozen=True, slots=True)
class RollbackResourceRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    target_version: str
    force: bool = False
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeprecateResourceVersionRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CreateBindingRequest:
    tenant_id: str
    subject_type: str
    subject_id: str
    resource_type: ResourceKind
    resource_id: str
    version_selector: str = "latest-published"
    credential_ref: str | None = None
    config: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishResourceResult:
    resource_id: str
    version: str
    status: str
    publish_id: str
    event_status: str
    kubernetes_workload_created: bool


@dataclass(frozen=True, slots=True)
class CreateApprovalRequest:
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    target_version: str
    operation: str = "rollback"
    reason: str | None = None
    ttl_seconds: float = 3600.0


@dataclass(frozen=True, slots=True)
class DecideApprovalRequest:
    tenant_id: str
    approval_id: str
    approved: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecordView:
    approval_id: str
    tenant_id: str
    kind: str
    resource_id: str
    target_version: str
    operation: str
    status: str
    requester_actor_id: str
    approver_actor_id: str | None
    reason: str | None
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None
