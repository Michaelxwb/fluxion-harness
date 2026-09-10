from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.models import AgentDefinitionModel
from framework.domain.agent import AgentDefinition
from framework.web.errors import AppError


class AgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    @staticmethod
    def to_domain(row: AgentDefinitionModel) -> AgentDefinition:
        """Translate a persisted row into the domain object (S-01 boundary).

        The adapter is the only layer that knows the table shape, so the
        Domain Model ↔ Repository Schema translation lives here. Binding
        collections (skill/knowledge/capability/service) are stored in their own
        tables and are not loaded by this mapping.
        """
        return AgentDefinition(
            id=row.id,
            name=row.name,
            description=row.description,
            instructions=row.instructions,
            model_config_ref=str(row.model_config_id) if row.model_config_id else "",
            memory_policy=row.memory_policy,
            revision=row.revision,
            enabled=row.enabled,
        )

    async def resolve(self, agent_id: UUID) -> AgentDefinition:
        """Load an agent and return it as a domain object (LIB-02)."""
        return self.to_domain(await self.get(agent_id))

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

    async def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        model_config_id: UUID | None,
        memory_policy: dict[str, Any],
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
                    description=description,
                    instructions=instructions,
                    model_config_id=model_config_id,
                    memory_policy=memory_policy,
                    enabled=True,
                )
                session.add(row)
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
                row.name = name
                row.description = description
                row.instructions = instructions
                row.model_config_id = model_config_id
                row.memory_policy = memory_policy
                row.revision += 1
                await session.flush()
                await session.refresh(row)
                return row

    async def set_enabled(self, agent_id: UUID, *, enabled: bool) -> None:
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

    async def soft_delete(self, agent_id: UUID) -> None:
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
                row.is_deleted = True
