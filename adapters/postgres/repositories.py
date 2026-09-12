from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import (
    ExecutionSnapshotModel,
    ServiceExecutionModel,
)
from framework.domain.execution import ExecutionSnapshot, ExecutionStatus, ServiceExecution
from framework.web.errors import AppError


def _execution_to_domain(row: ServiceExecutionModel) -> ServiceExecution:
    return ServiceExecution(
        id=row.id,
        actor_user_id=row.actor_user_id,
        service_id=row.service_id,
        service_release_id=row.service_release_id,
        snapshot_id=row.snapshot_id,
        resource_scope_json=row.resource_scope_json or {},
        input_json=row.input_json or {},
        status=ExecutionStatus(row.status),
        next_run_at=row.next_run_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        idempotency_key=row.idempotency_key,
        delivery_route_id=row.delivery_route_id,
        trace_id=row.trace_id,
    )


class SqlAlchemyExecutionRepository:
    """PostgreSQL-backed execution repository.

    The repository treats rows with ``is_deleted = true`` as nonexistent.
    Every mutation updates ``update_time`` through SQLAlchemy's onupdate rule.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def find_by_idempotency_key(self, key: str) -> ServiceExecution | None:
        async with self._session_factory() as session:
            stmt = select(ServiceExecutionModel).where(
                ServiceExecutionModel.idempotency_key == key,
                ServiceExecutionModel.is_deleted.is_(False),
            )
            row = await session.scalar(stmt)
            return _execution_to_domain(row) if row else None

    async def create(self, execution: ServiceExecution, snapshot: ExecutionSnapshot) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                snapshot_row = ExecutionSnapshotModel(
                    service_id=snapshot.service_id,
                    source=snapshot.source.value,
                    service_release_id=snapshot.service_release_id,
                    draft_revision=snapshot.draft_revision,
                    test_mode=snapshot.test_mode,
                    content_hash=snapshot.content_hash,
                    snapshot_schema_version=snapshot.snapshot_schema_version,
                    snapshot_json=snapshot.snapshot_json,
                    snapshot_ref=snapshot.snapshot_ref,
                )
                session.add(snapshot_row)
                await session.flush()

                session.add(
                    ServiceExecutionModel(
                        id=execution.id,
                        actor_user_id=execution.actor_user_id,
                        service_id=execution.service_id,
                        service_release_id=execution.service_release_id,
                        snapshot_id=snapshot_row.id,
                        execution_source=snapshot.source.value,
                        draft_revision=snapshot.draft_revision,
                        test_mode=snapshot.test_mode,
                        resource_scope_json=execution.resource_scope_json,
                        input_json=execution.input_json,
                        status=execution.status.value,
                        next_run_at=execution.next_run_at,
                        lease_owner=execution.lease_owner,
                        lease_expires_at=execution.lease_expires_at,
                        attempt=execution.attempt,
                        max_attempts=execution.max_attempts,
                        idempotency_key=execution.idempotency_key,
                        delivery_route_id=execution.delivery_route_id,
                        trace_id=execution.trace_id,
                    )
                )

    async def get(self, execution_id: UUID) -> ServiceExecution | None:
        async with self._session_factory() as session:
            stmt = select(ServiceExecutionModel).where(
                ServiceExecutionModel.id == execution_id,
                ServiceExecutionModel.is_deleted.is_(False),
            )
            row = await session.scalar(stmt)
            return _execution_to_domain(row) if row else None

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[ServiceExecution]:
        now = now or datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)

        eligible = or_(
            ServiceExecutionModel.status == ExecutionStatus.PENDING.value,
            and_(
                ServiceExecutionModel.status.in_(
                    [
                        ExecutionStatus.WAITING.value,
                        ExecutionStatus.RETRY_WAIT.value,
                    ]
                ),
                ServiceExecutionModel.next_run_at.is_not(None),
                ServiceExecutionModel.next_run_at <= now,
            ),
            and_(
                ServiceExecutionModel.status == ExecutionStatus.RUNNING.value,
                ServiceExecutionModel.lease_expires_at.is_not(None),
                ServiceExecutionModel.lease_expires_at <= now,
            ),
        )

        async with self._session_factory() as session:
            async with session.begin():
                stmt = (
                    select(ServiceExecutionModel)
                    .where(
                        ServiceExecutionModel.is_deleted.is_(False),
                        eligible,
                    )
                    .order_by(
                        ServiceExecutionModel.next_run_at.asc().nullsfirst(),
                        ServiceExecutionModel.create_time.asc(),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                rows = list((await session.scalars(stmt)).all())
                for row in rows:
                    row.status = ExecutionStatus.RUNNING.value
                    row.lease_owner = worker_id
                    row.lease_expires_at = lease_until
                await session.flush()
                return [_execution_to_domain(row) for row in rows]

    async def heartbeat(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                stmt = (
                    select(ServiceExecutionModel)
                    .where(
                        ServiceExecutionModel.id == execution_id,
                        ServiceExecutionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                row = await session.scalar(stmt)
                if row is None:
                    raise AppError(code="EXECUTION_NOT_FOUND", message="execution not found", status_code=404)
                if row.lease_owner != worker_id or row.status != ExecutionStatus.RUNNING.value:
                    raise AppError(
                        code="EXECUTION_LEASE_LOST",
                        message="execution lease is not owned by this worker",
                        status_code=409,
                    )
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)

    async def mark_waiting(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        next_run_at: datetime,
        status: ExecutionStatus = ExecutionStatus.WAITING,
    ) -> None:
        if status not in {ExecutionStatus.WAITING, ExecutionStatus.RETRY_WAIT}:
            raise ValueError("mark_waiting only accepts WAITING or RETRY_WAIT")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(ServiceExecutionModel)
                    .where(
                        ServiceExecutionModel.id == execution_id,
                        ServiceExecutionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AppError(code="EXECUTION_NOT_FOUND", message="execution not found", status_code=404)
                if row.lease_owner != worker_id:
                    raise AppError(code="EXECUTION_LEASE_LOST", message="execution lease lost", status_code=409)
                row.status = status.value
                row.next_run_at = next_run_at
                row.lease_owner = None
                row.lease_expires_at = None

    async def mark_terminal(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        status: ExecutionStatus,
    ) -> None:
        if status not in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }:
            raise ValueError("status is not terminal")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(ServiceExecutionModel)
                    .where(
                        ServiceExecutionModel.id == execution_id,
                        ServiceExecutionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AppError(code="EXECUTION_NOT_FOUND", message="execution not found", status_code=404)
                if row.lease_owner != worker_id:
                    raise AppError(code="EXECUTION_LEASE_LOST", message="execution lease lost", status_code=409)
                row.status = status.value
                row.next_run_at = None
                row.lease_owner = None
                row.lease_expires_at = None

    async def soft_delete(self, execution_id: UUID) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(ServiceExecutionModel)
                    .where(
                        ServiceExecutionModel.id == execution_id,
                        ServiceExecutionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row:
                    row.is_deleted = True
