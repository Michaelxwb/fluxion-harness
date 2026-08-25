from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
