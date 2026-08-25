from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.dml import Update

from fluxion.registry.schema import resource_definitions
from fluxion.registry.store import (
    NotFoundError,
    RegistryStoreError,
    VersionConflictError,
)
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    ResourceVisibility,
)


async def put(engine: AsyncEngine, definition: ResourceDefinition) -> ResourceDefinition:
    if definition.status is not ResourceStatus.DRAFT and definition.published_at is None:
        raise RegistryStoreError(
            f"non-draft resource {definition.tenant_id}/{definition.kind}/"
            f"{definition.id}@{definition.version} requires published_at"
        )
    values = _definition_values(definition)
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(resource_definitions).values(**values))
    except IntegrityError as exc:
        name = f"{definition.tenant_id}/{definition.kind}/{definition.id}@{definition.version}"
        raise VersionConflictError(f"{name} exists") from exc
    return definition


async def get(
    engine: AsyncEngine,
    kind: ResourceKind,
    resource_id: str,
    *,
    tenant_id: str,
    version: str | None = None,
) -> ResourceDefinition | None:
    statement = _select_definition(kind, resource_id, tenant_id, version)
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().first()
    return None if row is None else _definition_from_row(row)


async def publish(
    engine: AsyncEngine,
    kind: ResourceKind,
    resource_id: str,
    *,
    tenant_id: str,
    version: str,
) -> ResourceDefinition:
    async with engine.begin() as connection:
        current = (
            (
                await connection.execute(
                    _select_definition(kind, resource_id, tenant_id, version)
                )
            )
            .mappings()
            .first()
        )
        if current is None:
            raise NotFoundError(f"{tenant_id}/{kind}/{resource_id}@{version} not found")
        result = await connection.execute(
            _publish_definition(kind, resource_id, tenant_id, version)
        )
    if result.rowcount == 0:
        raise VersionConflictError(
            f"{tenant_id}/{kind}/{resource_id}@{version} already published or not draft"
        )
    published = await get(engine, kind, resource_id, tenant_id=tenant_id, version=version)
    if published is None:
        raise NotFoundError(f"{tenant_id}/{kind}/{resource_id}@{version} not found")
    return published


async def update_draft(
    engine: AsyncEngine,
    definition: ResourceDefinition,
) -> ResourceDefinition:
    statement = (
        update(resource_definitions)
        .where(resource_definitions.c.tenant_id == definition.tenant_id)
        .where(resource_definitions.c.kind == definition.kind.value)
        .where(resource_definitions.c.resource_id == definition.id)
        .where(resource_definitions.c.version == definition.version)
        .where(resource_definitions.c.status == ResourceStatus.DRAFT.value)
        .values(
            visibility=definition.visibility.value,
            spec_json=definition.spec_json,
        )
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement)
    if result.rowcount == 0:
        current = await get(
            engine,
            definition.kind,
            definition.id,
            tenant_id=definition.tenant_id,
            version=definition.version,
        )
        if current is None:
            raise NotFoundError(
                f"{definition.tenant_id}/{definition.kind}/{definition.id}@"
                f"{definition.version} not found"
            )
        raise VersionConflictError(f"{definition.id}@{definition.version} is not draft")
    updated = await get(
        engine,
        definition.kind,
        definition.id,
        tenant_id=definition.tenant_id,
        version=definition.version,
    )
    if updated is None:
        raise NotFoundError(
            f"{definition.tenant_id}/{definition.kind}/{definition.id}@"
            f"{definition.version} not found"
        )
    return updated


async def list_versions(
    engine: AsyncEngine,
    kind: ResourceKind,
    resource_id: str,
    *,
    tenant_id: str,
    offset: int,
    limit: int,
) -> tuple[list[ResourceDefinition], int]:
    scope = (
        resource_definitions.c.tenant_id == tenant_id,
        resource_definitions.c.kind == kind.value,
        resource_definitions.c.resource_id == resource_id,
    )
    items_statement = (
        select(resource_definitions)
        .where(*scope)
        .order_by(
            resource_definitions.c.created_at.desc(),
            resource_definitions.c.version.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    count_statement = select(func.count()).select_from(resource_definitions).where(*scope)
    async with engine.connect() as connection:
        rows = (await connection.execute(items_statement)).mappings().all()
        total = int((await connection.execute(count_statement)).scalar_one())
    return [_definition_from_row(row) for row in rows], total


async def list_resources(
    engine: AsyncEngine,
    kind: ResourceKind,
    *,
    tenant_id: str,
    offset: int,
    limit: int,
) -> tuple[list[ResourceDefinition], int]:
    return await _list_published_resources(
        engine,
        kind=kind,
        tenant_id=tenant_id,
        offset=offset,
        limit=limit,
    )


async def list_all_resources(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    offset: int,
    limit: int,
) -> tuple[list[ResourceDefinition], int]:
    # 单表 resource_definitions：不带 kind 过滤即列出租户下全部资源类型。
    return await _list_published_resources(
        engine,
        kind=None,
        tenant_id=tenant_id,
        offset=offset,
        limit=limit,
    )


async def _list_published_resources(
    engine: AsyncEngine,
    *,
    kind: ResourceKind | None,
    tenant_id: str,
    offset: int,
    limit: int,
) -> tuple[list[ResourceDefinition], int]:
    kind_scope = [resource_definitions.c.kind == kind.value] if kind is not None else []
    ranked = (
        select(
            *resource_definitions.c,
            func.row_number()
            .over(
                partition_by=resource_definitions.c.resource_id,
                order_by=(
                    resource_definitions.c.published_at.desc(),
                    resource_definitions.c.version.desc(),
                ),
            )
            .label("version_rank"),
        )
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(*kind_scope)
        .where(resource_definitions.c.status == ResourceStatus.PUBLISHED.value)
        .subquery()
    )
    items_statement = (
        select(ranked)
        .where(ranked.c.version_rank == 1)
        .order_by(ranked.c.resource_id.asc())
        .offset(offset)
        .limit(limit)
    )
    count_statement = (
        select(func.count(func.distinct(resource_definitions.c.resource_id)))
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(*kind_scope)
        .where(resource_definitions.c.status == ResourceStatus.PUBLISHED.value)
    )
    async with engine.connect() as connection:
        rows = (await connection.execute(items_statement)).mappings().all()
        total = int((await connection.execute(count_statement)).scalar_one())
    return [_definition_from_row(row) for row in rows], total


def _definition_values(definition: ResourceDefinition) -> dict[str, object]:
    return {
        "tenant_id": definition.tenant_id,
        "kind": definition.kind.value,
        "resource_id": definition.id,
        "version": definition.version,
        "status": definition.status.value,
        "visibility": definition.visibility.value,
        "spec_json": definition.spec_json,
        "created_at": definition.created_at,
        "published_at": definition.published_at,
    }


def _select_definition(
    kind: ResourceKind,
    resource_id: str,
    tenant_id: str,
    version: str | None,
) -> Select[tuple[object]]:
    statement = (
        select(resource_definitions)
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(resource_definitions.c.kind == kind.value)
        .where(resource_definitions.c.resource_id == resource_id)
    )
    if version is not None:
        return statement.where(resource_definitions.c.version == version)
    return (
        statement.where(resource_definitions.c.status == ResourceStatus.PUBLISHED.value)
        .order_by(resource_definitions.c.published_at.desc(), resource_definitions.c.version.desc())
        .limit(1)
    )


def _publish_definition(
    kind: ResourceKind,
    resource_id: str,
    tenant_id: str,
    version: str,
) -> Update:
    return (
        update(resource_definitions)
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(resource_definitions.c.kind == kind.value)
        .where(resource_definitions.c.resource_id == resource_id)
        .where(resource_definitions.c.version == version)
        .where(resource_definitions.c.status == ResourceStatus.DRAFT.value)
        .values(status=ResourceStatus.PUBLISHED.value, published_at=_now())
    )


def _definition_from_row(row: RowMapping) -> ResourceDefinition:
    spec_json = cast(dict[str, object], row["spec_json"])
    return ResourceDefinition(
        kind=ResourceKind(str(row["kind"])),
        id=str(row["resource_id"]),
        tenant_id=str(row["tenant_id"]),
        version=str(row["version"]),
        status=ResourceStatus(str(row["status"])),
        visibility=ResourceVisibility(str(row["visibility"])),
        spec_json=spec_json,
        created_at=cast(datetime, row["created_at"]),
        published_at=cast(datetime | None, row["published_at"]),
    )


def _now() -> datetime:
    return datetime.now(UTC)
