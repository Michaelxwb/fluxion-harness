from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import (
    AgentDefinitionModel,
    AgentServiceBindingModel,
    AuditLogModel,
    ServiceDefinitionModel,
    ServiceReleaseModel,
)
from framework.contracts.resource_scope import SERVICE_CONFIGURATION_INVALID
from framework.domain.publish import PublishedService, build_service_release
from framework.integration.resource_scope_registry import ResourceScopeRegistry
from framework.web.errors import AppError


class ServiceRepository:
    """PostgreSQL-backed Service definition/release repository.

    ``service_release`` rows are append-only: the single writer is
    :meth:`publish` (an INSERT). No UPDATE/DELETE path exists, and
    ``tests/architecture/test_release_immutable.py`` (E-02) fails the build if
    one is introduced anywhere under ``adapters/`` or ``framework/``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        scope_registry: ResourceScopeRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._scope_registry = scope_registry if scope_registry is not None else ResourceScopeRegistry()

    async def publish(
        self,
        service_id: UUID,
        *,
        published_by: UUID | None = None,
        request_id: str | None = None,
    ) -> ServiceReleaseModel:
        """Atomically validate + persist snapshot + switch current pointer.

        The whole sequence runs in one transaction. Republishing content that
        already has a release row reuses that row but **still switches**
        ``current_release_id``, so rolling a service back to an earlier payload
        is an explicit, observable pointer change rather than a silent no-op
        (S-02).
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
                    agent_snapshot=await self._freeze_bound_agents(session, service_id),
                    resource_scope_schema_hash=self._resolve_scope_schema_hash(service.draft_payload),
                )
                # The service row is locked FOR UPDATE above, so publishers of this
                # service serialize and this lookup cannot race another INSERT.
                existing = await session.scalar(
                    select(ServiceReleaseModel).where(
                        ServiceReleaseModel.service_id == service_id,
                        ServiceReleaseModel.content_hash == release.content_hash,
                        ServiceReleaseModel.is_deleted.is_(False),
                    )
                )
                if existing is not None:
                    return await self._switch_current_release(
                        session, service, release, existing, published_by, request_id
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
                await session.flush()
                service.current_release_id = row.id
                service.status = "published"
                await session.flush()
                self._record_published(session, service, release, row.id, published_by, request_id)
                await session.flush()
                await session.refresh(row)
                return row

    def _resolve_scope_schema_hash(self, draft: dict[str, object]) -> str | None:
        """Derive the frozen schema hash from the Integration manifest registry.

        RULE-05: the hash is never accepted from the caller, and an unknown
        ``resource_scope_type`` fails the publish before any row is written.
        Services that declare no scope stay publishable.
        """
        declared = draft.get("resource_scope_type")
        if declared is None:
            return None
        scope_type = str(declared)
        registered = self._scope_registry.get(scope_type)
        if registered is None:
            raise AppError(
                code=SERVICE_CONFIGURATION_INVALID,
                message=f"unknown resource_scope_type: {scope_type}",
                status_code=422,
            )
        return registered.schema_hash

    async def _freeze_bound_agents(self, session: AsyncSession, service_id: UUID) -> dict[str, object]:
        """Freeze the current config of every Agent bound to this Service (FEAT-02).

        D04 makes Agent edits direct-effect, so a published Service must carry
        its own copy of the Agent config it was published against. Keyed by
        agent id and ordered by id so the canonical content hash is reproducible.
        A Service with no bound Agent freezes an empty snapshot.
        """
        result = await session.execute(
            select(AgentDefinitionModel)
            .join(
                AgentServiceBindingModel,
                AgentServiceBindingModel.agent_id == AgentDefinitionModel.id,
            )
            .where(
                AgentServiceBindingModel.service_id == service_id,
                AgentServiceBindingModel.is_deleted.is_(False),
                AgentDefinitionModel.is_deleted.is_(False),
            )
            .order_by(AgentDefinitionModel.id)
        )
        return {
            str(agent.id): {
                "name": agent.name,
                "description": agent.description,
                "instructions": agent.instructions,
                "model_config_id": str(agent.model_config_id) if agent.model_config_id else None,
                "memory_policy": agent.memory_policy,
                "revision": agent.revision,
                "enabled": agent.enabled,
            }
            for agent in result.scalars().all()
        }

    async def _switch_current_release(
        self,
        session: AsyncSession,
        service: ServiceDefinitionModel,
        release: PublishedService,
        existing: ServiceReleaseModel,
        published_by: UUID | None,
        request_id: str | None,
    ) -> ServiceReleaseModel:
        """Point the service at an already-persisted release for identical content.

        Reached when the content hash already has a release row. If the pointer
        already targets that release this is a true no-op and emits no audit
        event; otherwise the pointer moves (rolling a service back to an earlier
        payload) and the move is audited.
        """
        if service.current_release_id == existing.id:
            return existing
        service.current_release_id = existing.id
        service.status = "published"
        await session.flush()
        self._record_published(session, service, release, existing.id, published_by, request_id)
        await session.flush()
        return existing

    @staticmethod
    def _record_published(
        session: AsyncSession,
        service: ServiceDefinitionModel,
        release: PublishedService,
        release_row_id: UUID,
        published_by: UUID | None,
        request_id: str | None,
    ) -> None:
        """Write the audit event; never stores the full sensitive payload (§3.5)."""
        session.add(
            AuditLogModel(
                actor_user_id=published_by,
                action="service.published",
                entity_type="service_release",
                entity_id=str(release_row_id),
                request_id=request_id,
                after_ref=release.release_id,
                details={
                    "service_key": service.service_key,
                    "release_id": release.release_id,
                    "content_hash": release.content_hash,
                },
            )
        )
