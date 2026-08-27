from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fluxion.registry.schema import (
    audit_logs,
    config_revisions,
    outbox_events,
    publish_records,
    resource_definitions,
)
from fluxion.registry.store import (
    AuditRecord,
    NotFoundError,
    OutboxEventRecord,
    OutboxStatus,
    PublicationCommand,
    PublicationCommit,
    PublicationOperation,
    RegistryStoreError,
    VersionConflictError,
)
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus, ResourceVisibility

AuditWriter = Callable[[AsyncConnection, AuditRecord], Awaitable[None]]


async def commit_publication(
    engine: AsyncEngine,
    command: PublicationCommand,
    audit_writer: AuditWriter,
) -> PublicationCommit:
    now = _now()
    async with engine.begin() as connection:
        current = await _locked_resource(connection, command)
        await _check_expected_base(connection, command)
        published = await _apply_operation(connection, command, current, now)
        revision = await _bump_revision(connection, command.tenant_id, now)
        audit = _audit_record(command, current, published, revision)
        await _insert_publish_record(connection, command, now)
        await audit_writer(connection, audit)
        await _insert_outbox(connection, command, revision, now)
    return PublicationCommit(
        resource=published,
        publish_id=command.publish_id,
        event_id=command.event_id,
        revision=revision,
        event_status=OutboxStatus.PENDING,
    )


async def insert_audit(connection: AsyncConnection, record: AuditRecord) -> None:
    await connection.execute(
        insert(audit_logs).values(
            audit_id=record.audit_id,
            tenant_id=record.tenant_id,
            actor_id=record.actor_id,
            request_id=record.request_id,
            publish_id=record.publish_id,
            action=record.action,
            target_type=record.target_type,
            target_id=record.target_id,
            before_json=record.before,
            after_json=record.after,
            created_at=record.created_at or _now(),
        )
    )


async def claim_outbox(
    engine: AsyncEngine,
    *,
    worker_id: str,
    limit: int,
    lease_seconds: float,
) -> list[OutboxEventRecord]:
    now = _now()
    lease_until = now + timedelta(seconds=lease_seconds)
    claimable = or_(
        and_(
            outbox_events.c.status == OutboxStatus.PENDING.value,
            outbox_events.c.available_at <= now,
        ),
        and_(
            outbox_events.c.status == OutboxStatus.PROCESSING.value,
            outbox_events.c.locked_until <= now,
        ),
    )
    statement = (
        select(outbox_events)
        .where(claimable)
        .order_by(outbox_events.c.created_at, outbox_events.c.event_id)
        .limit(limit)
        .with_for_update(skip_locked=engine.dialect.name == "postgresql")
    )
    async with engine.begin() as connection:
        rows = (await connection.execute(statement)).mappings().all()
        claimed: list[OutboxEventRecord] = []
        for row in rows:
            result = await connection.execute(
                update(outbox_events)
                .where(outbox_events.c.event_id == row["event_id"])
                .where(claimable)
                .values(
                    status=OutboxStatus.PROCESSING.value,
                    locked_by=worker_id,
                    locked_until=lease_until,
                )
            )
            if result.rowcount == 1:
                claimed.append(_outbox_from_row(row, OutboxStatus.PROCESSING))
    return claimed


async def mark_outbox_published(
    engine: AsyncEngine,
    event_id: str,
    *,
    worker_id: str,
) -> None:
    await _finish_outbox(
        engine,
        event_id,
        worker_id=worker_id,
        values={
            "status": OutboxStatus.PUBLISHED.value,
            "published_at": _now(),
            "locked_by": None,
            "locked_until": None,
            "last_error": None,
        },
    )


async def mark_outbox_retry(
    engine: AsyncEngine,
    event_id: str,
    *,
    worker_id: str,
    error: str,
    retry_at: datetime,
    terminal: bool,
) -> None:
    await _finish_outbox(
        engine,
        event_id,
        worker_id=worker_id,
        values={
            "status": OutboxStatus.FAILED.value if terminal else OutboxStatus.PENDING.value,
            "attempt_count": outbox_events.c.attempt_count + 1,
            "available_at": retry_at,
            "locked_by": None,
            "locked_until": None,
            "last_error": error[:1000],
        },
    )


async def _locked_resource(
    connection: AsyncConnection,
    command: PublicationCommand,
) -> RowMapping:
    statement = (
        select(resource_definitions)
        .where(resource_definitions.c.tenant_id == command.tenant_id)
        .where(resource_definitions.c.kind == command.kind.value)
        .where(resource_definitions.c.resource_id == command.resource_id)
        .where(resource_definitions.c.version == command.version)
        .with_for_update()
    )
    row = (await connection.execute(statement)).mappings().first()
    if row is None:
        raise NotFoundError(_resource_name(command))
    return row


async def _check_expected_base(
    connection: AsyncConnection,
    command: PublicationCommand,
) -> None:
    if command.operation is not PublicationOperation.PUBLISH:
        return
    if command.expected_base_version is None:
        return
    statement = (
        select(resource_definitions.c.version)
        .where(resource_definitions.c.tenant_id == command.tenant_id)
        .where(resource_definitions.c.kind == command.kind.value)
        .where(resource_definitions.c.resource_id == command.resource_id)
        .where(resource_definitions.c.status == ResourceStatus.PUBLISHED.value)
        .order_by(resource_definitions.c.published_at.desc(), resource_definitions.c.version.desc())
        .limit(1)
        # A6：CAS 读 latest published 须带行锁。PG READ COMMITTED 下，并发 publish
        # 阻塞于此；先提交者释放锁后，本 SELECT 重算 ORDER BY...LIMIT 1 指向新
        # latest，后到者读到更新后的 base 与 expected_base_version 不符 →
        # VersionConflict，CAS 原子生效。SQLite 方言省略 FOR UPDATE（靠
        # service-layer asyncio.Lock + StaticPool 单连接串行化），无副作用。
        .with_for_update()
    )
    row = (await connection.execute(statement)).first()
    current_base = command.version if row is None else str(row[0])
    if current_base != command.expected_base_version:
        raise VersionConflictError("version conflict")


async def _apply_operation(
    connection: AsyncConnection,
    command: PublicationCommand,
    current: RowMapping,
    now: datetime,
) -> ResourceDefinition:
    current_status = ResourceStatus(str(current["status"]))
    next_status = _next_status(command, current_status)
    values: dict[str, object] = {"status": next_status.value}
    if next_status is ResourceStatus.PUBLISHED:
        values["published_at"] = now
    result = await connection.execute(
        update(resource_definitions)
        .where(resource_definitions.c.tenant_id == command.tenant_id)
        .where(resource_definitions.c.kind == command.kind.value)
        .where(resource_definitions.c.resource_id == command.resource_id)
        .where(resource_definitions.c.version == command.version)
        .where(resource_definitions.c.status == current_status.value)
        .values(**values)
    )
    if result.rowcount != 1:
        raise VersionConflictError("version conflict")
    return _definition_from_row(current, status=next_status, published_at=values.get("published_at"))


def _next_status(
    command: PublicationCommand,
    current_status: ResourceStatus,
) -> ResourceStatus:
    if command.operation is PublicationOperation.PUBLISH:
        if current_status is not ResourceStatus.DRAFT:
            raise VersionConflictError("resource version is not draft")
        return ResourceStatus.PUBLISHED
    if command.operation is PublicationOperation.DEPRECATE:
        if current_status is not ResourceStatus.PUBLISHED:
            raise VersionConflictError("only published versions can be deprecated")
        return ResourceStatus.DEPRECATED
    if command.operation is PublicationOperation.TOMBSTONE:
        # ADR-SNAPSHOT-001 §3.2：PUBLISHED/DEPRECATED→TOMBSTONE（soft-delete 终态）；
        # DRAFT 未发布无 pinned payload 语义，TOMBSTONE 自身为终态不可重复进入。
        # REVIEW-C：tombstone 是高影响操作，与 rollback 对齐强制 approval_id。
        if current_status not in {ResourceStatus.PUBLISHED, ResourceStatus.DEPRECATED}:
            raise VersionConflictError("only published or deprecated versions can be tombstoned")
        if not command.approval_id:
            raise VersionConflictError("tombstone requires approval")
        return ResourceStatus.TOMBSTONE
    if current_status is ResourceStatus.DEPRECATED and not command.approval_id:
        raise VersionConflictError("deprecated rollback requires approval")
    if current_status not in {ResourceStatus.PUBLISHED, ResourceStatus.DEPRECATED}:
        raise VersionConflictError("rollback target must be historical published version")
    return ResourceStatus.PUBLISHED


async def _bump_revision(
    connection: AsyncConnection,
    tenant_id: str,
    now: datetime,
) -> int:
    values = {"tenant_id": tenant_id, "revision": 1, "updated_at": now}
    updates = {"revision": config_revisions.c.revision + 1, "updated_at": now}
    if connection.dialect.name == "postgresql":
        pg_upsert = (
            postgresql_insert(config_revisions)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[config_revisions.c.tenant_id],
                set_=updates,
            )
            .returning(config_revisions.c.revision)
        )
        row = (await connection.execute(pg_upsert)).first()
    else:
        sqlite_upsert = (
            sqlite_insert(config_revisions)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[config_revisions.c.tenant_id],
                set_=updates,
            )
            .returning(config_revisions.c.revision)
        )
        row = (await connection.execute(sqlite_upsert)).first()
    if row is None:
        raise RegistryStoreError(f"failed to bump revision for tenant {tenant_id}")
    return int(row[0])


async def _insert_publish_record(
    connection: AsyncConnection,
    command: PublicationCommand,
    now: datetime,
) -> None:
    await connection.execute(
        insert(publish_records).values(
            publish_id=command.publish_id,
            tenant_id=command.tenant_id,
            resource_type=command.kind.value,
            resource_id=command.resource_id,
            version=command.version,
            operation=command.operation.value,
            actor_id=command.actor_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
            event_id=command.event_id,
            publish_note=command.publish_note,
            approval_id=command.approval_id,
            created_at=now,
        )
    )


async def _insert_outbox(
    connection: AsyncConnection,
    command: PublicationCommand,
    revision: int,
    now: datetime,
) -> None:
    payload = {
        "event_id": command.event_id,
        "publish_id": command.publish_id,
        "tenant_id": command.tenant_id,
        "kind": command.kind.value,
        "resource_id": command.resource_id,
        "version": command.version,
        "revision": revision,
        "operation": command.operation.value,
    }
    await connection.execute(
        insert(outbox_events).values(
            event_id=command.event_id,
            tenant_id=command.tenant_id,
            event_type="config.changed",
            aggregate_type=command.kind.value,
            aggregate_id=command.resource_id,
            version=command.version,
            revision=revision,
            payload_json=payload,
            status=OutboxStatus.PENDING.value,
            attempt_count=0,
            available_at=now,
            locked_by=None,
            locked_until=None,
            last_error=None,
            created_at=now,
            published_at=None,
        )
    )


def _audit_record(
    command: PublicationCommand,
    before: RowMapping,
    after: ResourceDefinition,
    revision: int,
) -> AuditRecord:
    return AuditRecord(
        audit_id=f"audit_{command.publish_id}",
        tenant_id=command.tenant_id,
        actor_id=command.actor_id,
        request_id=command.request_id,
        publish_id=command.publish_id,
        action=command.operation.value,
        target_type=command.kind.value,
        target_id=command.resource_id,
        before={"version": str(before["version"]), "status": str(before["status"])},
        after={
            "version": after.version,
            "status": after.status.value,
            "publish_id": command.publish_id,
            "revision": revision,
            "approval_id": command.approval_id,
        },
    )


async def _finish_outbox(
    engine: AsyncEngine,
    event_id: str,
    *,
    worker_id: str,
    values: dict[str, object],
) -> None:
    statement = (
        update(outbox_events)
        .where(outbox_events.c.event_id == event_id)
        .where(outbox_events.c.status == OutboxStatus.PROCESSING.value)
        .where(outbox_events.c.locked_by == worker_id)
        .values(**values)
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement)
    if result.rowcount != 1:
        raise RegistryStoreError(f"outbox event {event_id} is not owned by {worker_id}")


def _outbox_from_row(row: RowMapping, status: OutboxStatus) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=str(row["event_id"]),
        tenant_id=str(row["tenant_id"]),
        event_type=str(row["event_type"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        version=str(row["version"]),
        revision=int(row["revision"]),
        payload=cast(dict[str, object], row["payload_json"]),
        status=status,
        attempt_count=int(row["attempt_count"]),
        available_at=cast(datetime, row["available_at"]),
    )


def _definition_from_row(
    row: RowMapping,
    *,
    status: ResourceStatus,
    published_at: object,
) -> ResourceDefinition:
    return ResourceDefinition(
        kind=ResourceKind(str(row["kind"])),
        id=str(row["resource_id"]),
        tenant_id=str(row["tenant_id"]),
        version=str(row["version"]),
        status=status,
        visibility=ResourceVisibility(str(row["visibility"])),
        spec_json=cast(dict[str, object], row["spec_json"]),
        created_at=cast(datetime, row["created_at"]),
        published_at=cast(datetime | None, published_at or row["published_at"]),
    )


def _resource_name(command: PublicationCommand) -> str:
    return f"{command.tenant_id}/{command.kind.value}/{command.resource_id}@{command.version}"


def _now() -> datetime:
    return datetime.now(UTC)
