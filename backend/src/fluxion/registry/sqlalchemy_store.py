from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from fluxion.observability.tracing import traced_scope
from fluxion.registry import (
    channel_sqlalchemy,
    publish_sqlalchemy,
    resource_sqlalchemy,
    retention_sqlalchemy,
    user_sqlalchemy,
    workflow_run_sqlalchemy,
)
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
    outbox_events,
    resource_bindings,
)
from fluxion.registry.store import (
    DEFAULT_HARD_DELETE_RETENTION,
    ActiveReference,
    AuditRecord,
    BindingCommand,
    BindingCommit,
    BindingOperation,
    DeleteResult,
    NotFoundError,
    OutboxEventRecord,
    OutboxStatus,
    PublicationCommand,
    PublicationCommit,
    RegistryStoreError,
    ScopedReadStore,
    ScopedReadTimeoutError,
    ScopedRegistryReader,
    VersionConflictError,
)
from fluxion.registry.user_store import CapabilityGrantRecord
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
)


class _ScopedRegistryReader:
    """一致视图 reader（ADR-A016）：绑定租户 + 固定 revision。

    只读三方法（配置读）；Credential/Memory/外部 I/O 不在此（事务外分段）。
    同包行映射复用（resource_sqlalchemy），不重复列逻辑。
    """

    def __init__(
        self, connection: AsyncConnection, tenant_id: str, revision: int
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self._revision = revision

    def _check_tenant(self, tenant_id: str) -> None:
        if tenant_id != self._tenant_id:
            raise ValueError(
                f"scoped read tenant mismatch: scope={self._tenant_id} "
                f"request={tenant_id}（禁止跨租户复用一致视图）"
            )

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        self._check_tenant(tenant_id)
        row = (
            (
                await self._connection.execute(
                    resource_sqlalchemy._select_definition(
                        kind, resource_id, tenant_id, version
                    )
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else resource_sqlalchemy._definition_from_row(row)

    async def read_revision(self) -> int:
        return self._revision

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        self._check_tenant(tenant_id)
        rows = (
            (
                await self._connection.execute(
                    _select_bindings(subject_type, subject_id, tenant_id, resource_type)
                )
            )
            .mappings()
            .all()
        )
        return [_binding_from_row(row) for row in rows]

    async def get_user_profile_at(
        self, *, tenant_id: str, platform_user_id: str, version: str
    ) -> dict[str, object] | None:
        self._check_tenant(tenant_id)
        return await user_sqlalchemy.fetch_profile_at(
            self._connection,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            version=version,
        )

    async def get_latest_user_profile(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None:
        self._check_tenant(tenant_id)
        return await user_sqlalchemy.fetch_latest_profile(
            self._connection,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
        )

    async def list_capability_grants(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[CapabilityGrantRecord]:
        self._check_tenant(tenant_id)
        rows = await user_sqlalchemy.fetch_grants(
            self._connection, tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return [
            CapabilityGrantRecord(
                id=int(r["id"]),
                tenant_id=r["tenant_id"],
                platform_user_id=r["platform_user_id"],
                capability_ref=r["capability_ref"],
                capability_kind=r.get("capability_kind", "skill"),
                granted_scope=r["granted_scope"],
                version_pin=r["version_pin"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        resource_id: str | None = None,
        status: ResourceStatus | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        self._check_tenant(tenant_id)
        return await resource_sqlalchemy._list_resource_rows(
            self._connection,
            kind=kind,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
            published_only=True,
            keyword=keyword,
            resource_id=resource_id,
            status=status,
        )


class SQLAlchemyRegistryStore(ScopedReadStore):
    def __init__(self, dsn: str, *, reset_on_initialize: bool = False) -> None:
        if not dsn.startswith("postgresql"):
            raise ValueError(f"registry DSN must use postgresql (got {dsn!r})")
        self._dsn = dsn
        self._reset_on_initialize = reset_on_initialize
        self._engine = create_async_engine(dsn, **self._engine_kwargs())

    @staticmethod
    def _engine_kwargs() -> dict[str, object]:
        # PG-Only（ADR-A007）：构造器已拒绝非 postgresql DSN，此处只配 PG。
        ssl_mode = os.environ.get("FLUXION_POSTGRES_SSL", "disable")
        kwargs: dict[str, object] = {
            "connect_args": {"command_timeout": 2.0, "ssl": ssl_mode},
            "pool_pre_ping": True,
        }
        # Phase 6 TASK-001：连接池可配置（默认 SQLAlchemy 5+10 在满负载
        # scale-test / 多副本生产下排队成瓶颈）；FLUXION_PG_POOL_SIZE 显式
        # 覆盖 pool_size（max_overflow 同步放大，保持弹性）。
        pool_size = os.environ.get("FLUXION_PG_POOL_SIZE")
        if pool_size is not None and pool_size.isdigit() and int(pool_size) > 0:
            size = int(pool_size)
            kwargs["pool_size"] = size
            kwargs["max_overflow"] = size
        return kwargs

    async def initialize(self) -> None:
        # A13/ADR-004 + ADR-A007：schema 事实源唯一是 scripts/init_db.py。
        # - serving（reset=False）：no-op，不建表（已移除 alembic）。
        # - reset_on_initialize=True（契约测试 bootstrap）：drop_all + create_all
        #   重建干净库。
        if not self._reset_on_initialize:
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    async def put(self, definition: ResourceDefinition) -> ResourceDefinition:
        # O506（TASK-008）：DB span 经 traced_scope（kind/id 入 attributes）
        async with traced_scope(
            "db.query",
            attributes={
                "db.operation": "put",
                "fluxion.resource_kind": definition.kind.value,
                "fluxion.resource_id": definition.id,
            },
        ):
            return await resource_sqlalchemy.put(self._engine, definition)

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        async with traced_scope(
            "db.query",
            attributes={
                "db.operation": "get",
                "fluxion.resource_kind": kind.value,
                "fluxion.resource_id": resource_id,
            },
        ):
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
        keyword: str | None = None,
        resource_id: str | None = None,
        status: ResourceStatus | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        return await resource_sqlalchemy.list_resources(
            self._engine,
            kind,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
            keyword=keyword,
            resource_id=resource_id,
            status=status,
        )

    async def list_all_resources(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        resource_id: str | None = None,
        status: ResourceStatus | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        return await resource_sqlalchemy.list_all_resources(
            self._engine,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
            keyword=keyword,
            resource_id=resource_id,
            status=status,
        )

    async def list_current_resources(
        self,
        kind: ResourceKind | None,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        resource_id: str | None = None,
        status: ResourceStatus | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        return await resource_sqlalchemy.list_current_resources(
            self._engine,
            kind,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
            keyword=keyword,
            resource_id=resource_id,
            status=status,
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
        action: str | None = None,
        actor_id: str | None = None,
        target_type: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> tuple[list[AuditRecord], int]:
        # TASK-021（§8.10）：过滤条件下推 SQL（时间范围走后端查询参数，不做前端全量过滤）
        conditions = [audit_logs.c.tenant_id == tenant_id]
        if action is not None:
            conditions.append(audit_logs.c.action == action)
        if actor_id is not None:
            conditions.append(audit_logs.c.actor_id == actor_id)
        if target_type is not None:
            conditions.append(audit_logs.c.target_type == target_type)
        if created_from is not None:
            conditions.append(audit_logs.c.created_at >= created_from)
        if created_to is not None:
            conditions.append(audit_logs.c.created_at <= created_to)
        statement = (
            select(audit_logs)
            .where(*conditions)
            # F8：同事务批量写入的 audit created_at 相同，缺 tiebreak 会跨页重复/丢失。
            # audit_id 作确定性次序键，保证分页稳定。
            .order_by(audit_logs.c.created_at.desc(), audit_logs.c.audit_id.desc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(audit_logs).where(*conditions)
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

    async def commit_binding(self, command: BindingCommand) -> BindingCommit:
        # A12：Binding 治理事务——insert/update binding + bump_revision + audit +
        # outbox 收进单事务（镜像 commit_publication）。此前 put_binding/
        # disable_binding 先提交 binding 再单独 bump_revision（两步间崩溃则
        # revision 不变、轮询型 runtime 看不到新 binding），且根本不写 outbox
        # （跨 Pod 权限生效/收回延迟到下一次 publish）。audit 内联进事务使 A20
        # 的 fail-closed 对 binding 真正生效——此前 audit 在 binding 提交后跑
        # 独立事务，失败时 binding 已落地，fail-closed 只是装饰性。
        now = _now()
        async with self._engine.begin() as connection:
            before: dict[str, object] | None
            after: dict[str, object]
            if command.operation is BindingOperation.CREATE:
                if command.binding is None:
                    raise RegistryStoreError("binding command missing binding for grant")
                try:
                    await connection.execute(
                        insert(resource_bindings).values(**_binding_values(command.binding))
                    )
                except IntegrityError as exc:
                    raise VersionConflictError(
                        f"binding {command.binding_id} exists"
                    ) from exc
                before = None
                after = {
                    "subject_type": str(command.binding.subject_type),
                    "subject_id": command.binding.subject_id,
                    "resource_type": command.binding.resource_type.value,
                    "resource_id": command.binding.resource_id,
                    "version_selector": command.binding.resource_version_selector,
                    "enabled": command.binding.enabled,
                }
                binding = command.binding
            else:  # DISABLE：先 SELECT FOR UPDATE 取 before 态，再 update
                lock_statement = (
                    select(resource_bindings)
                    .where(resource_bindings.c.binding_id == command.binding_id)
                    .where(resource_bindings.c.tenant_id == command.tenant_id)
                    .with_for_update()
                )
                row = (await connection.execute(lock_statement)).mappings().first()
                if row is None:
                    raise NotFoundError(f"binding {command.binding_id} not found")
                before = {
                    "subject_type": str(row["subject_type"]),
                    "subject_id": str(row["subject_id"]),
                    "resource_type": str(row["resource_type"]),
                    "resource_id": str(row["resource_id"]),
                    "enabled": bool(row["enabled"]),
                }
                result = await connection.execute(
                    update(resource_bindings)
                    .where(resource_bindings.c.binding_id == command.binding_id)
                    .where(resource_bindings.c.tenant_id == command.tenant_id)
                    .values(enabled=False)
                )
                if result.rowcount != 1:
                    raise NotFoundError(f"binding {command.binding_id} not found")
                after = {"enabled": False}
                binding = _binding_from_row(row).model_copy(update={"enabled": False})
            revision = await publish_sqlalchemy._bump_revision(
                connection, command.tenant_id, now
            )
            await self._insert_audit(
                connection,
                AuditRecord(
                    audit_id=f"audit_{command.event_id}",
                    tenant_id=command.tenant_id,
                    actor_id=command.actor_id,
                    request_id=command.request_id,
                    publish_id=None,
                    action=command.operation.value,
                    target_type="binding",
                    target_id=command.binding_id,
                    before=before,
                    after=after,
                    created_at=now,
                ),
            )
            await connection.execute(
                insert(outbox_events).values(
                    event_id=command.event_id,
                    tenant_id=command.tenant_id,
                    event_type="config.changed",
                    aggregate_type="binding",
                    aggregate_id=command.binding_id,
                    version=binding.resource_version_selector,
                    revision=revision,
                    payload_json={
                        "event_id": command.event_id,
                        "tenant_id": command.tenant_id,
                        "binding_id": command.binding_id,
                        "operation": command.operation.value,
                        "resource_type": binding.resource_type.value,
                        "resource_id": binding.resource_id,
                        "revision": revision,
                    },
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
        return BindingCommit(
            binding=binding,
            event_id=command.event_id,
            revision=revision,
            event_status=OutboxStatus.PENDING,
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

    @asynccontextmanager
    async def begin_scoped_read(
        self, *, tenant_id: str, timeout_ms: int = 5_000
    ) -> AsyncIterator[ScopedRegistryReader]:
        """一致读入口（ADR-A016）：REPEATABLE READ 只读视图 + 固定 revision。

        同一 scope 内全部配置读落同一快照（混合发布窗口不可见）；外部 I/O
        禁止持有本连接（调用方分段）。连接拿不到即类型化超时，无无穷等待。
        """
        branched = self._engine.execution_options(isolation_level="REPEATABLE READ")
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                connection_cm = branched.connect()
                connection = await connection_cm.__aenter__()
        except TimeoutError as exc:
            raise ScopedReadTimeoutError(
                f"scoped_read_timeout: 一致读连接 {timeout_ms}ms 未拿到"
            ) from exc
        try:
            row = (
                await connection.execute(
                    select(config_revisions.c.revision).where(
                        config_revisions.c.tenant_id == tenant_id
                    )
                )
            ).first()
            revision = int(row[0]) if row is not None else 0
            yield _ScopedRegistryReader(connection, tenant_id, revision)
        finally:
            await connection.close()

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
        resource_type: ResourceKind | None = None,
        resource_id: str | None = None,
        subject_type: str | None = None,
    ) -> tuple[list[ResourceBinding], int]:
        conditions = [resource_bindings.c.tenant_id == tenant_id]
        if resource_type is not None:
            conditions.append(resource_bindings.c.resource_type == resource_type.value)
        # TASK-013：Agent 授权投影按 resource_id + subject_type 过滤（user→agent）
        if resource_id is not None:
            conditions.append(resource_bindings.c.resource_id == resource_id)
        if subject_type is not None:
            conditions.append(resource_bindings.c.subject_type == subject_type)
        statement = (
            select(resource_bindings)
            .where(*conditions)
            # F8：同事务批量写入的 binding created_at 相同，缺 tiebreak 会跨页重复/丢失。
            .order_by(
                resource_bindings.c.created_at.desc(),
                resource_bindings.c.binding_id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        count_statement = (
            select(func.count()).select_from(resource_bindings).where(*conditions)
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

    async def recall_pinned(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
    ) -> ResourceDefinition:
        return await resource_sqlalchemy.recall_pinned(
            self._engine,
            kind,
            resource_id,
            tenant_id=tenant_id,
            version=version,
        )

    async def hard_delete(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str,
        approval_id: str,
        retention_period: timedelta = DEFAULT_HARD_DELETE_RETENTION,
    ) -> DeleteResult:
        return await retention_sqlalchemy.hard_delete(
            self._engine,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            approval_id=approval_id,
            retention_period=retention_period,
        )

    async def add_active_reference(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        await resource_sqlalchemy.add_active_reference(
            self._engine,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    async def release_active_reference(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        await resource_sqlalchemy.release_active_reference(
            self._engine,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    async def release_active_references_for_ref(
        self,
        *,
        tenant_id: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        """按 ref_id（workflow run_id）释放该 run 的全部引用（TASK-007 terminal GC）。"""
        await resource_sqlalchemy.release_active_references_for_ref(
            self._engine,
            tenant_id=tenant_id,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    async def check_active_references(
        self,
        *,
        tenant_id: str,
        kind: ResourceKind,
        resource_id: str,
        version: str,
        ref_type: str | None = None,
    ) -> list[ActiveReference]:
        return await resource_sqlalchemy.check_active_references(
            self._engine,
            tenant_id=tenant_id,
            kind=kind,
            resource_id=resource_id,
            version=version,
            ref_type=ref_type,
        )

    # ---- TASK-008：workflow_run 投影（FEAT-P3-06，API/Console 读路径）----

    async def upsert_workflow_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        workflow_id: str,
        workflow_version: int,
        execution_id: str,
        trace_id: str,
        pinned_refs: list[dict[str, str]],
        status: str = "running",
        node_states: dict[str, object] | None = None,
    ) -> None:
        """幂等 upsert run 投影行（writer 侧为 worker psycopg，本方法是 async 契约侧）。"""
        await workflow_run_sqlalchemy.upsert_workflow_run(
            self._engine,
            tenant_id=tenant_id,
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            execution_id=execution_id,
            trace_id=trace_id,
            pinned_refs=pinned_refs,
            status=status,
            node_states=node_states,
        )

    async def get_workflow_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
    ) -> RowMapping | None:
        """按 (tenant_id, run_id) 读取投影（tenant scope，RULE-P3-06）。"""
        return await workflow_run_sqlalchemy.get_workflow_run(
            self._engine, tenant_id=tenant_id, run_id=run_id
        )

    async def list_workflow_runs(
        self,
        *,
        tenant_id: str,
        workflow_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RowMapping], int]:
        """tenant 强制 scope 的 run 列表 + 总数（一次取回，无 N+1）。

        workflow_id=None → 跨工作流 list-all（Phase 5 TASK-011）。
        """
        return await workflow_run_sqlalchemy.list_workflow_runs(
            self._engine,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            limit=limit,
            offset=offset,
        )

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
        self, *, tenant_id: str, offset: int, limit: int, keyword: str | None = None
    ) -> tuple[list[PlatformUserRecord], int]:
        return await channel_sqlalchemy.list_platform_users(
            self._engine,
            tenant_id=tenant_id,
            offset=offset,
            limit=limit,
            keyword=keyword,
        )

    async def create_chat_access(self, record: ChatAccessRecord) -> ChatAccessRecord:
        return await channel_sqlalchemy.create_chat_access(self._engine, record)

    async def resolve_chat_access(self, *, token_hash: str) -> ChatAccessRecord | None:
        return await channel_sqlalchemy.resolve_chat_access(self._engine, token_hash=token_hash)

    async def list_chat_access(
        self,
        *,
        tenant_id: str,
        platform_user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[ChatAccessRecord]:
        return await channel_sqlalchemy.list_chat_access(
            self._engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
        )

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

    async def resolve_platform_user_by_channel_id(
        self, *, tenant_id: str, channel_user_id: str
    ) -> str | None:
        return await channel_sqlalchemy.resolve_platform_user_by_channel_id(
            self._engine, tenant_id=tenant_id, channel_user_id=channel_user_id
        )

    async def redeem_bind_code(self, redemption: BindRedemption) -> ChannelIdentityRecord:
        return await channel_sqlalchemy.redeem_bind_code(self._engine, redemption)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine


    # ---- User Domain（Gate 1B）门面：组合 user_sqlalchemy + engine 暴露 ----

    async def put_user_profile(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        profile_json: dict[str, object],
    ) -> int:
        return await user_sqlalchemy.put_profile(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            profile_json=profile_json,
        )

    async def get_latest_user_profile(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, Any] | None:
        return await user_sqlalchemy.get_latest_profile(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )

    async def get_user_profile_at(
        self, *, tenant_id: str, platform_user_id: str, version: str
    ) -> dict[str, Any] | None:
        return await user_sqlalchemy.get_profile_at(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            version=version,
        )

    async def put_user_preferences(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        preference_json: dict[str, object],
    ) -> dict[str, Any]:
        await user_sqlalchemy.put_preferences(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            preference_json=preference_json,
        )
        row = await user_sqlalchemy.get_preferences(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        assert row is not None
        return row

    async def get_user_preferences(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, Any] | None:
        return await user_sqlalchemy.get_preferences(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )

    async def upsert_profile_attribute(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        attribute: dict[str, object],
    ) -> dict[str, Any]:
        return await user_sqlalchemy.upsert_profile_attribute(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            attribute=dict(attribute),
        )

    async def list_profile_attributes(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[dict[str, Any]]:
        return await user_sqlalchemy.list_profile_attributes(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )

    async def delete_profile_attribute(
        self, *, tenant_id: str, platform_user_id: str, key: str
    ) -> int:
        return await user_sqlalchemy.delete_profile_attribute(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id, key=key
        )

    async def add_capability_grant(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        capability_ref: str,
        granted_scope: str,
        version_pin: str | None,
        capability_kind: str = "skill",
    ) -> CapabilityGrantRecord:
        created = await user_sqlalchemy.add_grant(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
            capability_kind=capability_kind,
            granted_scope=granted_scope,
            version_pin=version_pin,
        )
        return CapabilityGrantRecord(
            id=created,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
            capability_kind=capability_kind,
            granted_scope=granted_scope,
            version_pin=version_pin,
            created_at=user_sqlalchemy._now(),
        )

    async def list_capability_grants(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[CapabilityGrantRecord]:
        rows = await user_sqlalchemy.list_grants(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return [
            CapabilityGrantRecord(
                id=int(r["id"]),
                tenant_id=r["tenant_id"],
                platform_user_id=r["platform_user_id"],
                capability_ref=r["capability_ref"],
                capability_kind=r.get("capability_kind", "skill"),
                granted_scope=r["granted_scope"],
                version_pin=r["version_pin"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def revoke_capability_grant(
        self, *, tenant_id: str, platform_user_id: str, capability_ref: str
    ) -> int:
        return await user_sqlalchemy.revoke_grant(
            self.engine,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
        )

    async def list_channel_identities_for_user(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[dict[str, Any]]:
        return await user_sqlalchemy.list_channel_identities_for_user(
            self.engine, tenant_id=tenant_id, platform_user_id=platform_user_id
        )


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
