from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from fluxion.registry import channel_sqlalchemy, publish_sqlalchemy, resource_sqlalchemy
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
        return await resource_sqlalchemy.put(self._engine, definition)

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        return await resource_sqlalchemy.get(
            self._engine,
            kind,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )

    async def publish(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition:
        return await resource_sqlalchemy.publish(
            self._engine,
            kind,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )

    async def update_draft(self, definition: ResourceDefinition) -> ResourceDefinition:
        return await resource_sqlalchemy.update_draft(self._engine, definition)

    async def list_versions(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]:
        return await resource_sqlalchemy.list_versions(
            self._engine,
            kind,
            resource_id,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
        )

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinition], int]:
        return await resource_sqlalchemy.list_resources(
            self._engine,
            kind,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
        )

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
