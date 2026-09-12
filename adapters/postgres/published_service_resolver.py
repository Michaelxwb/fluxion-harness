from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import ServiceDefinitionModel, ServiceReleaseModel
from framework.execution.service import PublishedServiceResolver, ResolvedRelease
from framework.web.errors import AppError


class SqlPublishedServiceResolver(PublishedServiceResolver):
    """Resolve the current published release of a service key from PostgreSQL.

    Returns a :class:`ResolvedRelease` (service_id / service_release_id /
    content_hash / published_payload). Unpublished or disabled services are
    execution-time errors, not 404s, so the caller can surface a stable error
    code to the proposing Agent.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, service_key: str) -> ResolvedRelease:
        async with self._session_factory() as session:
            service = await session.scalar(
                select(ServiceDefinitionModel).where(
                    ServiceDefinitionModel.key == service_key,
                    ServiceDefinitionModel.is_deleted.is_(False),
                )
            )
            if service is None or not service.enabled:
                raise AppError(
                    code="SERVICE_NOT_AVAILABLE",
                    message=f"service not found or disabled: {service_key}",
                    status_code=404,
                )
            release = await session.scalar(
                select(ServiceReleaseModel).where(
                    ServiceReleaseModel.id == service.current_release_id,
                    ServiceReleaseModel.is_deleted.is_(False),
                )
            )
            if release is None:
                raise AppError(
                    code="SERVICE_NOT_PUBLISHED",
                    message=f"service has no published release: {service_key}",
                    status_code=409,
                )
            payload = release.published_payload if isinstance(release.published_payload, dict) else {}
            return ResolvedRelease(
                service_id=service.id,
                service_release_id=release.id,
                content_hash=release.content_hash,
                published_payload=payload,
            )
