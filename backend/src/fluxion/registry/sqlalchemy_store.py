from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.dml import Update

from fluxion.registry import channel_sqlalchemy, publish_sqlalchemy
from fluxion.registry.channel_store import (
    BindCodeRecord,
    BindRedemption,
    ChannelIdentityRecord,
    ChatAccessRecord,
    PlatformUserRecord,
)
from fluxion.registry.schema import (
    audit_logs,
    config_revisions,
    metadata,
    resource_bindings,
    resource_definitions,
)
from fluxion.registry.store import (
    AuditRecord,
    NotFoundError,
    OutboxEventRecord,
    PublicationCommand,
    PublicationCommit,
    RegistryStoreError,
    VersionConflictError,
)
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    ResourceVisibility,
)


class SQLAlchemyRegistryStore:
    def __init__(self, dsn: str, *, reset_on_initialize: bool = False) -> None:
        self._dsn = dsn
        self._reset_on_initialize = reset_on_initialize
        self._engine = create_async_engine(dsn, **self._engine_kwargs(dsn))

    @staticmethod
    def _engine_kwargs(dsn: str) -> dict[str, object]:
        if dsn.startswith("sqlite") and ":memory:" in dsn:
            return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        if dsn.startswith("postgresql"):
            ssl_mode = os.environ.get("FLUXION_POSTGRES_SSL", "disable")
            return {
                "connect_args": {"command_timeout": 2.0, "ssl": ssl_mode},
                "pool_pre_ping": True,
            }
        return {}

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            if self._reset_on_initialize:
                await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    async def put(self, definition: ResourceDefinition) -> ResourceDefinition:
        if definition.status is not ResourceStatus.DRAFT and definition.published_at is None:
            raise RegistryStoreError(
                f"non-draft resource {definition.tenant_id}/{definition.kind}/"
                f"{definition.id}@{definition.version} requires published_at"
            )
        values = _definition_values(definition)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert(resource_definitions).values(**values))
        except IntegrityError as exc:
            name = f"{definition.tenant_id}/{definition.kind}/{definition.id}@{definition.version}"
            raise VersionConflictError(f"{name} exists") from exc
        return definition

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        statement = _select_definition(kind, resource_id, tenant_id, version)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return None if row is None else _definition_from_row(row)

    async def publish(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition:
        async with self._engine.begin() as connection:
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
        published = await self.get(kind, resource_id, tenant_id=tenant_id, version=version)
        if published is None:
            raise NotFoundError(f"{tenant_id}/{kind}/{resource_id}@{version} not found")
        return published

    async def update_draft(self, definition: ResourceDefinition) -> ResourceDefinition:
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
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
        if result.rowcount == 0:
            current = await self.get(
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
        updated = await self.get(
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
        self,
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
        async with self._engine.connect() as connection:
            rows = (await connection.execute(items_statement)).mappings().all()
            total = int((await connection.execute(count_statement)).scalar_one())
        return [_definition_from_row(row) for row in rows], total

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]:
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
            .where(resource_definitions.c.kind == kind.value)
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
            .where(resource_definitions.c.kind == kind.value)
            .where(resource_definitions.c.status == ResourceStatus.PUBLISHED.value)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(items_statement)).mappings().all()
            total = int((await connection.execute(count_statement)).scalar_one())
        return [_definition_from_row(row) for row in rows], total

    async def append_audit(self, record: AuditRecord) -> None:
        async with self._engine.begin() as connection:
            await self._insert_audit(connection, record)

    async def list_audit(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[AuditRecord], int]:
        statement = (
            select(audit_logs)
            .where(audit_logs.c.tenant_id == tenant_id)
            .order_by(audit_logs.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(audit_logs).where(
            audit_logs.c.tenant_id == tenant_id
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
            total = int((await connection.execute(count_statement)).scalar_one())
        return [_audit_from_row(row) for row in rows], total

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        record: AuditRecord,
    ) -> None:
        await publish_sqlalchemy.insert_audit(connection, record)

    async def commit_publication(self, command: PublicationCommand) -> PublicationCommit:
        return await publish_sqlalchemy.commit_publication(
            self._engine,
            command,
            self._insert_audit,
        )

    async def claim_outbox(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: float,
    ) -> list[OutboxEventRecord]:
        return await publish_sqlalchemy.claim_outbox(
            self._engine,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def mark_outbox_published(self, event_id: str, *, worker_id: str) -> None:
        await publish_sqlalchemy.mark_outbox_published(
            self._engine,
            event_id,
            worker_id=worker_id,
        )

    async def mark_outbox_retry(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: datetime,
        terminal: bool,
    ) -> None:
        await publish_sqlalchemy.mark_outbox_retry(
            self._engine,
            event_id,
            worker_id=worker_id,
            error=error,
            retry_at=retry_at,
            terminal=terminal,
        )

    async def read_revision(self, *, tenant_id: str) -> int:
        statement = select(config_revisions.c.revision).where(
            config_revisions.c.tenant_id == tenant_id
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        if row is None:
            return 0
        return int(row[0])

    async def bump_revision(self, *, tenant_id: str) -> int:
        for _attempt in range(2):
            async with self._engine.begin() as connection:
                result = await connection.execute(
                    update(config_revisions)
                    .where(config_revisions.c.tenant_id == tenant_id)
                    .values(revision=config_revisions.c.revision + 1)
                    .returning(config_revisions.c.revision)
                )
                row = result.mappings().first()
                if row is not None:
                    return int(row["revision"])
            try:
                async with self._engine.begin() as connection:
                    await connection.execute(
                        insert(config_revisions).values(
                            tenant_id=tenant_id,
                            revision=1,
                            updated_at=_now(),
                        )
                    )
                return 1
            except IntegrityError:
                continue
        raise RegistryStoreError(f"failed to bump revision for tenant {tenant_id}")

    async def put_binding(self, binding: ResourceBinding) -> ResourceBinding:
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    insert(resource_bindings).values(**_binding_values(binding))
                )
        except IntegrityError as exc:
            raise VersionConflictError(f"binding {binding.binding_id} exists") from exc
        await self.bump_revision(tenant_id=binding.tenant_id)
        return binding

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        statement = _select_bindings(subject_type, subject_id, tenant_id, resource_type)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_binding_from_row(row) for row in rows]

    async def list_bindings_page(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceBinding], int]:
        statement = (
            select(resource_bindings)
            .where(resource_bindings.c.tenant_id == tenant_id)
            .order_by(resource_bindings.c.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(resource_bindings).where(
            resource_bindings.c.tenant_id == tenant_id
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
            total = int((await connection.execute(count_statement)).scalar_one())
        return [_binding_from_row(row) for row in rows], total

    async def disable_binding(self, binding_id: str, *, tenant_id: str) -> None:
        statement = (
            update(resource_bindings)
            .where(resource_bindings.c.binding_id == binding_id)
            .where(resource_bindings.c.tenant_id == tenant_id)
            .values(enabled=False)
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
        if result.rowcount == 0:
            raise NotFoundError(f"binding {binding_id} not found")
        await self.bump_revision(tenant_id=tenant_id)

    async def create_platform_user(self, record: PlatformUserRecord) -> PlatformUserRecord:
        return await channel_sqlalchemy.create_platform_user(self._engine, record)

    async def get_platform_user(
        self, *, tenant_id: str, platform_user_id: str
    ) -> PlatformUserRecord | None:
        return await channel_sqlalchemy.get_platform_user(
            self._engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
        )

    async def list_platform_users(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[PlatformUserRecord], int]:
        return await channel_sqlalchemy.list_platform_users(
            self._engine,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
        )

    async def create_chat_access(self, record: ChatAccessRecord) -> ChatAccessRecord:
        return await channel_sqlalchemy.create_chat_access(self._engine, record)

    async def resolve_chat_access(self, *, token_hash: str) -> ChatAccessRecord | None:
        return await channel_sqlalchemy.resolve_chat_access(self._engine, token_hash=token_hash)

    async def revoke_chat_access(
        self, *, tenant_id: str, access_id: str, revoked_at: datetime
    ) -> ChatAccessRecord:
        return await channel_sqlalchemy.revoke_chat_access(
            self._engine,
            tenant_id=tenant_id,
            access_id=access_id,
            revoked_at=revoked_at,
        )

    async def create_bind_code(self, record: BindCodeRecord) -> BindCodeRecord:
        return await channel_sqlalchemy.create_bind_code(self._engine, record)

    async def resolve_channel_identity(
        self, *, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord | None:
        return await channel_sqlalchemy.resolve_channel_identity(
            self._engine,
            tenant_id=tenant_id,
            channel_type=channel_type,
            channel_user_id=channel_user_id,
        )

    async def redeem_bind_code(self, redemption: BindRedemption) -> ChannelIdentityRecord:
        return await channel_sqlalchemy.redeem_bind_code(self._engine, redemption)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine


class SQLiteRegistryStore(SQLAlchemyRegistryStore):
    pass


class PostgreSQLRegistryStore(SQLAlchemyRegistryStore):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


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


def _binding_values(binding: ResourceBinding) -> dict[str, object]:
    raw_subject_type = (
        binding.subject_type.value
        if hasattr(binding.subject_type, "value")
        else binding.subject_type
    )
    subject_type = str(raw_subject_type)
    return {
        "binding_id": binding.binding_id,
        "tenant_id": binding.tenant_id,
        "subject_type": subject_type,
        "subject_id": binding.subject_id,
        "resource_type": binding.resource_type.value,
        "resource_id": binding.resource_id,
        "resource_version_selector": binding.resource_version_selector,
        "config_json": binding.config_json,
        "credential_ref": binding.credential_ref,
        "enabled": binding.enabled,
        "created_at": binding.created_at,
    }


def _select_bindings(
    subject_type: str,
    subject_id: str,
    tenant_id: str,
    resource_type: ResourceKind | None,
) -> Select[tuple[Any]]:
    statement = (
        select(resource_bindings)
        .where(resource_bindings.c.tenant_id == tenant_id)
        .where(resource_bindings.c.subject_type == subject_type)
        .where(resource_bindings.c.subject_id == subject_id)
        .where(resource_bindings.c.enabled.is_(True))
        .order_by(resource_bindings.c.created_at.asc(), resource_bindings.c.binding_id.asc())
    )
    if resource_type is None:
        return statement
    return statement.where(resource_bindings.c.resource_type == resource_type.value)


def _binding_from_row(row: RowMapping) -> ResourceBinding:
    return ResourceBinding(
        binding_id=str(row["binding_id"]),
        tenant_id=str(row["tenant_id"]),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        resource_type=ResourceKind(str(row["resource_type"])),
        resource_id=str(row["resource_id"]),
        resource_version_selector=str(row["resource_version_selector"]),
        config_json=cast(dict[str, object] | None, row["config_json"]),
        credential_ref=cast(str | None, row["credential_ref"]),
        enabled=bool(row["enabled"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _audit_from_row(row: RowMapping) -> AuditRecord:
    return AuditRecord(
        audit_id=str(row["audit_id"]),
        tenant_id=str(row["tenant_id"]),
        actor_id=str(row["actor_id"]),
        request_id=str(row["request_id"]),
        publish_id=None if row["publish_id"] is None else str(row["publish_id"]),
        action=str(row["action"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        before=cast(dict[str, object] | None, row["before_json"]),
        after=cast(dict[str, object] | None, row["after_json"]),
        created_at=cast(datetime, row["created_at"]),
    )
