from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import AuditLogModel, ServiceDefinitionModel, ServiceReleaseModel
from framework.domain.publish import PublishedService, build_service_release
from framework.web.errors import AppError


class ServiceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def publish(
        self,
        service_id: UUID,
        *,
        agent_snapshot: dict[str, object] | None = None,
        resource_scope_type: str | None = None,
        resource_scope_schema_hash: str | None = None,
        published_by: UUID | None = None,
        request_id: str | None = None,
    ) -> ServiceReleaseModel:
        """Atomically validate + persist snapshot + switch current pointer.

        validate + snapshot persist + current pointer switch happen in one
        transaction; republishing identical content returns the existing
        release instead of failing (idempotent).
        """
        async with self._session_factory() as session:
            async with session.begin():
                service = await session.scalar(
                    select(ServiceDefinitionModel)
                    .where(
                        ServiceDefinitionModel.id == service_id,
                        ServiceDefinitionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if service is None:
                    raise AppError(code="SERVICE_NOT_FOUND", message="service not found", status_code=404)
                if not service.draft_payload:
                    raise AppError(
                        code="SERVICE_DRAFT_REQUIRED",
                        message="service has no draft to publish",
                        status_code=409,
                    )
                release = build_service_release(
                    service_id=service_id,
                    service_key=service.service_key,
                    draft=service.draft_payload,
                    agent_snapshot=agent_snapshot,
                    resource_scope_type=resource_scope_type,
                    resource_scope_schema_hash=resource_scope_schema_hash,
                )
                row = ServiceReleaseModel(
                    service_id=service_id,
                    release_id=release.release_id,
                    content_hash=release.content_hash,
                    published_payload=release.frozen_payload,
                    published_by=published_by,
                    published_at=datetime.now(UTC),
                )
                session.add(row)
                try:
                    async with session.begin_nested():
                        await session.flush()
                except IntegrityError:
                    if row in session:
                        session.expunge(row)
                    return await self._existing_release(service_id, release)
                service.current_release_id = row.id
                service.status = "published"
                await session.flush()
                session.add(
                    AuditLogModel(
                        actor_user_id=published_by,
                        action="service.published",
                        entity_type="service_release",
                        entity_id=str(row.id),
                        request_id=request_id,
                        after_ref=release.release_id,
                        details={
                            "service_key": service.service_key,
                            "release_id": release.release_id,
                            "content_hash": release.content_hash,
                        },
                    )
                )
                await session.flush()
                await session.refresh(row)
                return row

    async def _existing_release(self, service_id: UUID, release: PublishedService) -> ServiceReleaseModel:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ServiceReleaseModel).where(
                    ServiceReleaseModel.service_id == service_id,
                    ServiceReleaseModel.content_hash == release.content_hash,
                    ServiceReleaseModel.is_deleted.is_(False),
                )
            )
            if row is None:  # pragma: no cover - defensive; constraint guarantees presence
                raise AppError(
                    code="SERVICE_PUBLISH_CONFLICT",
                    message="service release already exists",
                    status_code=409,
                )
            return row
