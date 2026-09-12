from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import (
    AgentCapabilityBindingModel,
    AgentDefinitionModel,
    AgentKnowledgeBindingModel,
    AgentServiceBindingModel,
    AgentSkillBindingModel,
    AuditLogModel,
)
from framework.domain.agent import AgentDefinition
from framework.web.errors import AppError


class AgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    @staticmethod
    def to_domain(
        row: AgentDefinitionModel,
        *,
        skill_bindings: Sequence[UUID] = (),
        knowledge_bindings: Sequence[UUID] = (),
        capability_bindings: Sequence[UUID] = (),
        service_bindings: Sequence[UUID] = (),
    ) -> AgentDefinition:
        """Translate a row **plus its binding rows** into the domain aggregate.

        Bindings live in four separate tables, so a row alone cannot produce a
        complete ``AgentDefinition``. Every binding set must be passed
        explicitly; callers that only have the row must use :meth:`resolve` so
        an incomplete aggregate can never be built by omission.
        """
        return AgentDefinition(
            id=row.id,
            name=row.name,
            description=row.description,
            instructions=row.instructions,
            model_config_ref=str(row.model_config_id) if row.model_config_id else "",
            skill_bindings=[str(item) for item in skill_bindings],
            knowledge_bindings=[str(item) for item in knowledge_bindings],
            capability_bindings=[str(item) for item in capability_bindings],
            service_bindings=[str(item) for item in service_bindings],
            memory_policy=row.memory_policy,
            revision=row.revision,
            enabled=row.enabled,
        )

    async def resolve(self, agent_id: UUID) -> AgentDefinition:
        """Load an agent with all four binding sets as a full aggregate (LIB-02)."""
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentDefinitionModel).where(
                    AgentDefinitionModel.id == agent_id,
                    AgentDefinitionModel.is_deleted.is_(False),
                )
            )
            if row is None:
                raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
            skills = (
                await session.scalars(
                    select(AgentSkillBindingModel.skill_id).where(
                        AgentSkillBindingModel.agent_id == agent_id,
                        AgentSkillBindingModel.is_deleted.is_(False),
                    )
                )
            ).all()
            knowledge = (
                await session.scalars(
                    select(AgentKnowledgeBindingModel.knowledge_source_id).where(
                        AgentKnowledgeBindingModel.agent_id == agent_id,
                        AgentKnowledgeBindingModel.is_deleted.is_(False),
                    )
                )
            ).all()
            capabilities = (
                await session.scalars(
                    select(AgentCapabilityBindingModel.capability_id).where(
                        AgentCapabilityBindingModel.agent_id == agent_id,
                        AgentCapabilityBindingModel.is_deleted.is_(False),
                    )
                )
            ).all()
            services = (
                await session.scalars(
                    select(AgentServiceBindingModel.service_id).where(
                        AgentServiceBindingModel.agent_id == agent_id,
                        AgentServiceBindingModel.is_deleted.is_(False),
                    )
                )
            ).all()
        return self.to_domain(
            row,
            skill_bindings=skills,
            knowledge_bindings=knowledge,
            capability_bindings=capabilities,
            service_bindings=services,
        )

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str | None = None,
        enabled: bool | None = None,
    ) -> tuple[list[AgentDefinitionModel], int]:
        predicates = [AgentDefinitionModel.is_deleted.is_(False)]
        if keyword:
            predicates.append(AgentDefinitionModel.name.ilike(f"%{keyword}%"))
        if enabled is not None:
            predicates.append(AgentDefinitionModel.enabled.is_(enabled))

        async with self._session_factory() as session:
            count_stmt = select(func.count()).select_from(AgentDefinitionModel).where(*predicates)
            total = int((await session.scalar(count_stmt)) or 0)
            stmt = (
                select(AgentDefinitionModel)
                .where(*predicates)
                .order_by(AgentDefinitionModel.update_time.desc(), AgentDefinitionModel.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = list((await session.scalars(stmt)).all())
            return rows, total

    async def get(self, agent_id: UUID) -> AgentDefinitionModel:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentDefinitionModel).where(
                    AgentDefinitionModel.id == agent_id,
                    AgentDefinitionModel.is_deleted.is_(False),
                )
            )
            if row is None:
                raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
            return row

    @staticmethod
    def _record_audit(
        session: AsyncSession,
        *,
        action: str,
        resource_id: UUID,
        revision: int,
        actor_user_id: UUID | None,
        request_id: str | None,
    ) -> None:
        """Write one audit event per management action (02 RULE-06).

        ``details`` carries only revision identity — never the full payload
        (RULE-06: details 禁完整 payload).
        """
        session.add(
            AuditLogModel(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="agent_definition",
                resource_id=str(resource_id),
                request_id=request_id,
                after_ref=f"r{revision}",
                details={"revision": revision},
            )
        )

    async def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        model_config_id: UUID | None,
        memory_policy: dict[str, Any],
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> AgentDefinitionModel:
        async with self._session_factory() as session:
            async with session.begin():
                duplicate = await session.scalar(
                    select(AgentDefinitionModel.id).where(
                        AgentDefinitionModel.name == name,
                        AgentDefinitionModel.is_deleted.is_(False),
                    )
                )
                if duplicate is not None:
                    raise AppError(
                        code="AGENT_NAME_CONFLICT", message="agent name already exists", status_code=409
                    )
                row = AgentDefinitionModel(
                    name=name,
                    agent_key=name,
                    description=description,
                    instructions=instructions,
                    model_config_id=model_config_id,
                    memory_policy=memory_policy,
                    enabled=True,
                )
                session.add(row)
                await session.flush()
                self._record_audit(
                    session,
                    action="agent.created",
                    resource_id=row.id,
                    revision=row.revision,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
                await session.flush()
                await session.refresh(row)
                return row

    async def save(
        self,
        agent_id: UUID,
        *,
        name: str,
        description: str,
        instructions: str,
        model_config_id: UUID | None,
        memory_policy: dict[str, Any],
        expected_revision: int,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> AgentDefinitionModel:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(AgentDefinitionModel)
                    .where(
                        AgentDefinitionModel.id == agent_id,
                        AgentDefinitionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
                # Optimistic concurrency: the caller must state the revision it
                # read. Without this, two managers editing the same Agent
                # silently clobber each other (last write wins, no signal).
                if row.revision != expected_revision:
                    raise AppError(
                        code="AGENT_REVISION_CONFLICT",
                        message=(
                            f"agent revision changed: expected {expected_revision}, found {row.revision}"
                        ),
                        status_code=409,
                    )
                row.name = name
                row.description = description
                row.instructions = instructions
                row.model_config_id = model_config_id
                row.memory_policy = memory_policy
                row.revision += 1
                await session.flush()
                self._record_audit(
                    session,
                    action="agent.saved",
                    resource_id=row.id,
                    revision=row.revision,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
                await session.flush()
                await session.refresh(row)
                return row

    async def set_enabled(
        self,
        agent_id: UUID,
        *,
        enabled: bool,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(AgentDefinitionModel)
                    .where(
                        AgentDefinitionModel.id == agent_id,
                        AgentDefinitionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AppError(code="AGENT_NOT_FOUND", message="agent not found", status_code=404)
                row.enabled = enabled
                self._record_audit(
                    session,
                    action="agent.enabled" if enabled else "agent.disabled",
                    resource_id=row.id,
                    revision=row.revision,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )

    async def soft_delete(
        self,
        agent_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(AgentDefinitionModel)
                    .where(
                        AgentDefinitionModel.id == agent_id,
                        AgentDefinitionModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if row is None:
                    return
                self._record_audit(
                    session,
                    action="agent.deleted",
                    resource_id=row.id,
                    revision=row.revision,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
                row.is_deleted = True
