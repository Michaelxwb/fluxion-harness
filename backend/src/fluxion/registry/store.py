from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from fluxion.resources import ResourceBinding, ResourceDefinition, ResourceKind


class RegistryStoreError(RuntimeError):
    """Base error for RegistryStore adapters."""


class NotFoundError(RegistryStoreError):
    """Requested tenant-scoped registry object does not exist."""


class VersionConflictError(RegistryStoreError):
    """Versioned resource or binding already exists."""


class PublicationOperation(StrEnum):
    PUBLISH = "publish"
    ROLLBACK = "rollback"
    DEPRECATE = "deprecate"
    # ADR-SNAPSHOT-001：soft-delete（PUBLISHED/DEPRECATED→TOMBSTONE），走同一治理
    # 事务（audit + publish_record + outbox + revision）。
    TOMBSTONE = "tombstone"


class BindingOperation(StrEnum):
    # 值即 AuditRecord.action，保持既有 wire 契约（"binding.create"/"binding.disable"）。
    CREATE = "binding.create"
    DISABLE = "binding.disable"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: str
    tenant_id: str
    actor_id: str
    request_id: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, object] | None
    after: dict[str, object] | None
    publish_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublicationCommand:
    publish_id: str
    event_id: str
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    operation: PublicationOperation
    actor_id: str
    request_id: str
    trace_id: str
    expected_base_version: str | None = None
    publish_note: str | None = None
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationCommit:
    resource: ResourceDefinition
    publish_id: str
    event_id: str
    revision: int
    event_status: OutboxStatus


@dataclass(frozen=True, slots=True)
class BindingCommand:
    """Binding 治理事务命令（A12，镜像 PublicationCommand）。

    GRANT（CREATE）：携带 `binding`（完整 ResourceBinding，含服务层生成的
    binding_id），事务内 insert + audit + outbox + revision 原子化。
    DISABLE：`binding=None`，事务内 SELECT FOR UPDATE 既有行（取 before 态供
    审计）+ update enabled=False + audit + outbox + revision。
    actor_id/request_id/trace_id 流入 audit + outbox，与 publish 治理一致。
    """

    event_id: str
    tenant_id: str
    binding_id: str
    operation: BindingOperation
    actor_id: str
    request_id: str
    trace_id: str
    binding: ResourceBinding | None = None  # GRANT 必填；DISABLE 为 None


@dataclass(frozen=True, slots=True)
class BindingCommit:
    binding: ResourceBinding
    event_id: str
    revision: int
    event_status: OutboxStatus


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    event_id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    version: str
    revision: int
    payload: dict[str, object]
    status: OutboxStatus
    attempt_count: int
    available_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveReference:
    """pinned 版本的一条 active 引用（ADR-SNAPSHOT-001；坐标由查询 scope 决定）。"""

    ref_type: str
    ref_id: str
    created_at: datetime


# ADR-SNAPSHOT-001 RISK-02：retention_period 默认保守语义——Phase 6 前不因
# retention 放行（guard 逻辑就位，值以参数注入；S-03 用 timedelta(0) 验证通过路径）。
DEFAULT_HARD_DELETE_RETENTION: timedelta = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """hard_delete 治理提交结果（镜像 PublicationCommit 形态）。"""

    publish_id: str
    event_id: str
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    version: str
    revision: int
    event_status: OutboxStatus


@runtime_checkable
class RegistryReadStore(Protocol):
    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None: ...

    async def read_revision(self, *, tenant_id: str) -> int: ...

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]: ...


@runtime_checkable
class RegistryStore(RegistryReadStore, Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def put(self, definition: ResourceDefinition) -> ResourceDefinition: ...

    async def publish(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition: ...

    async def update_draft(self, definition: ResourceDefinition) -> ResourceDefinition: ...

    async def recall_pinned(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition: ...

    async def hard_delete(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
        approval_id: str,
        retention_period: timedelta = DEFAULT_HARD_DELETE_RETENTION,
    ) -> DeleteResult: ...

    async def list_versions(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]: ...

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]: ...

    async def list_all_resources(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]: ...

    async def append_audit(self, record: AuditRecord) -> None: ...

    async def list_audit(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[AuditRecord], int]: ...

    async def commit_publication(self, command: PublicationCommand) -> PublicationCommit: ...

    async def commit_binding(self, command: BindingCommand) -> BindingCommit: ...

    async def claim_outbox(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: float,
    ) -> list[OutboxEventRecord]: ...

    async def mark_outbox_published(self, event_id: str, *, worker_id: str) -> None: ...

    async def mark_outbox_retry(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: datetime,
        terminal: bool,
    ) -> None: ...

    async def bump_revision(self, *, tenant_id: str) -> int: ...

    async def put_binding(self, binding: ResourceBinding) -> ResourceBinding: ...

    async def list_bindings_page(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        resource_type: ResourceKind | None = None,
    ) -> tuple[list[ResourceBinding], int]: ...

    async def disable_binding(self, binding_id: str, *, tenant_id: str) -> None: ...

    async def add_active_reference(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str,
        ref_id: str,
    ) -> None: ...

    async def release_active_reference(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str,
        ref_id: str,
    ) -> None: ...

    async def check_active_references(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str | None = None,
    ) -> list[ActiveReference]: ...
