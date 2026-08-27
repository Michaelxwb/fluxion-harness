"""ADR-SNAPSHOT-001：pinned 版本 hard-delete 治理（三重 guard + GC safety）。

`hard_delete` 走既有治理（audit + publish_records + outbox + revision，A8/A9/A20
模式）：固定 guard 顺序 active_ref → retention_period → GC safety check（删除
事务内二次确认）。失败返回 `active_reference_blocked` / `retention_period_not_elapsed`
/ `gc_safety_check_failed` 码且行保留；全过则物理删除 `resource_definitions` 行。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fluxion.registry import publish_sqlalchemy
from fluxion.registry.resource_sqlalchemy import select_active_references
from fluxion.registry.schema import outbox_events, publish_records, resource_definitions
from fluxion.registry.store import (
    AuditRecord,
    DeleteResult,
    NotFoundError,
    OutboxStatus,
    RegistryStoreError,
    VersionConflictError,
)
from fluxion.resources import ResourceKind, ResourceStatus


async def hard_delete(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    approval_id: str,
    retention_period: timedelta,
) -> DeleteResult:
    """物理删除 tombstoned 版本（三重 guard + GC safety 二次确认）。

    guard 顺序：active_ref → retention_period → GC safety（删除事务内二次确认）。
    顺序重复删除在预检命中 NotFound（NFR-REL-03 幂等）；并发败方在 GC safety/
    CAS 删除 rowcount=0 命中 gc_safety_check_failed（无孤儿/重复治理）。
    """
    # 1. 乐观预检（独立读事务）：存在 + TOMBSTONE + active_ref + retention
    async with engine.connect() as connection:
        row = await _select_definition(connection, tenant_id, kind, resource_id, version)
        if row is None:
            raise NotFoundError(f"{tenant_id}/{kind.value}/{resource_id}@{version} not found")
        if str(row["status"]) != ResourceStatus.TOMBSTONE.value:
            raise VersionConflictError("only tombstoned versions can be hard deleted")
        refs = await select_active_references(
            connection,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
        )
        if refs:
            raise RegistryStoreError(
                f"active_reference_blocked: {len(refs)} active reference(s) on "
                f"{tenant_id}/{kind.value}/{resource_id}@{version}"
            )
        tombstoned_at = await _tombstoned_at(connection, tenant_id, kind, resource_id, version)
        now = _now()
        if (
            tombstoned_at is None
            or (_as_aware(tombstoned_at) + retention_period) > now
        ):
            raise RegistryStoreError(
                f"retention_period_not_elapsed: {tenant_id}/{kind.value}/{resource_id}@{version}"
            )

    # 2. 删除事务 + GC safety 二次确认（E-02 race）
    publish_id = f"hd_{uuid4().hex}"
    event_id = f"evt_hd_{uuid4().hex}"
    async with engine.begin() as connection:
        # REVIEW-A：先对 resource_definitions 父行 SELECT ... FOR UPDATE，与
        # add_active_reference 的 FOR SHARE 互斥——PG 下 add 若先持有共享锁并插入
        # 引用，此处阻塞至 add 提交后重算，re-check 读到引用 → gc_safety_check_failed；
        # add 若后到则阻塞至此事务提交（行已删），随后 FOR SHARE 读到父行缺失而失败。
        # SQLite 方言忽略 FOR UPDATE，靠文件锁 + busy_timeout + 下方 CAS rowcount 兜底。
        await _select_definition(
            connection, tenant_id, kind, resource_id, version, for_update=True
        )
        # GC safety check：二次确认无残留引用（并发 race 失败方在此被捕获）
        race_refs = await select_active_references(
            connection,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
        )
        if race_refs:
            raise RegistryStoreError(
                f"gc_safety_check_failed: active reference appeared during hard_delete of "
                f"{tenant_id}/{kind.value}/{resource_id}@{version}"
            )
        # 物理删除 + CAS（status 仍为 TOMBSTONE）：rowcount=0 即并发 winner 已删除/状态已变
        result = await connection.execute(
            delete(resource_definitions)
            .where(resource_definitions.c.tenant_id == tenant_id)
            .where(resource_definitions.c.kind == kind.value)
            .where(resource_definitions.c.resource_id == resource_id)
            .where(resource_definitions.c.version == version)
            .where(resource_definitions.c.status == ResourceStatus.TOMBSTONE.value)
        )
        if result.rowcount == 0:
            raise RegistryStoreError(
                f"gc_safety_check_failed: {tenant_id}/{kind.value}/{resource_id}@{version} "
                "no longer tombstoned or already deleted"
            )
        revision = await publish_sqlalchemy._bump_revision(connection, tenant_id, now)
        await _insert_hard_delete_governance(
            connection,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            approval_id=approval_id,
            publish_id=publish_id,
            event_id=event_id,
            revision=revision,
            now=now,
        )
    return DeleteResult(
        publish_id=publish_id,
        event_id=event_id,
        tenant_id=tenant_id,
        kind=kind,
        resource_id=resource_id,
        version=version,
        revision=revision,
        event_status=OutboxStatus.PENDING,
    )


async def _select_definition(
    connection: AsyncConnection,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    *,
    for_update: bool = False,
) -> RowMapping | None:
    statement = (
        select(resource_definitions.c.status)
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(resource_definitions.c.kind == kind.value)
        .where(resource_definitions.c.resource_id == resource_id)
        .where(resource_definitions.c.version == version)
    )
    if for_update:
        statement = statement.with_for_update()
    return (await connection.execute(statement)).mappings().first()


async def _tombstoned_at(
    connection: AsyncConnection,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
) -> datetime | None:
    statement = (
        select(publish_records.c.created_at)
        .where(publish_records.c.tenant_id == tenant_id)
        .where(publish_records.c.resource_type == kind.value)
        .where(publish_records.c.resource_id == resource_id)
        .where(publish_records.c.version == version)
        .where(publish_records.c.operation == "tombstone")
        .order_by(publish_records.c.created_at.desc())
        .limit(1)
    )
    row = (await connection.execute(statement)).first()
    return None if row is None else row[0]


async def _insert_hard_delete_governance(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    approval_id: str,
    publish_id: str,
    event_id: str,
    revision: int,
    now: datetime,
) -> None:
    # console 层接线时由调用方传入真实 actor；registry 层 hard_delete 以 approval_id
    # 关联审批，actor_id 暂记 "system"（审计 row 闭合，不阻塞 TASK-003 范围）。
    await publish_sqlalchemy.insert_audit(
        connection,
        AuditRecord(
            audit_id=f"audit_{publish_id}",
            tenant_id=tenant_id,
            actor_id="system",
            request_id=publish_id,
            publish_id=publish_id,
            action="hard_delete",
            target_type=kind.value,
            target_id=resource_id,
            before={"version": version, "status": ResourceStatus.TOMBSTONE.value},
            after={
                "version": version,
                "deleted": True,
                "publish_id": publish_id,
                "revision": revision,
                "approval_id": approval_id,
            },
            created_at=now,
        ),
    )
    await connection.execute(
        insert(publish_records).values(
            publish_id=publish_id,
            tenant_id=tenant_id,
            resource_type=kind.value,
            resource_id=resource_id,
            version=version,
            operation="hard_delete",
            actor_id="system",
            request_id=publish_id,
            trace_id=publish_id,
            event_id=event_id,
            publish_note=None,
            approval_id=approval_id,
            created_at=now,
        )
    )
    payload = {
        "event_id": event_id,
        "publish_id": publish_id,
        "tenant_id": tenant_id,
        "kind": kind.value,
        "resource_id": resource_id,
        "version": version,
        "revision": revision,
        "operation": "hard_delete",
    }
    await connection.execute(
        insert(outbox_events).values(
            event_id=event_id,
            tenant_id=tenant_id,
            event_type="config.changed",
            aggregate_type=kind.value,
            aggregate_id=resource_id,
            version=version,
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


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    """SQLite 经 aiosqlite 读 DateTime(timezone=True) 列返回 naive；PG 返回 aware。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
