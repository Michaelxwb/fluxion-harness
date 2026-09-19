import uuid

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import (
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)

USER_SCOPE_ALL = "ALL"


def current_artifact_join() -> ColumnElement[bool]:
    return and_(
        SkillArtifact.id == Skill.current_artifact_id,
        SkillArtifact.is_deleted.is_(False),
    )


class SkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_skills(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        user_scope: str | None,
        execution_mode: str | None,
    ) -> tuple[list[tuple[Skill, SkillArtifact | None]], int]:
        conditions = [Skill.tenant_id == tenant_id, Skill.is_deleted.is_(False)]
        if user_scope is not None:
            conditions.append(Skill.user_scope == user_scope)
        if execution_mode is not None:
            conditions.append(SkillArtifact.execution_mode == execution_mode)
        total = await self._session.scalar(
            select(func.count())
            .select_from(Skill)
            .outerjoin(SkillArtifact, current_artifact_join())
            .where(*conditions)
        )
        result = await self._session.execute(
            select(Skill, SkillArtifact)
            .outerjoin(SkillArtifact, current_artifact_join())
            .where(*conditions)
            .order_by(Skill.update_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(skill, artifact) for skill, artifact in result.all()], int(total or 0)

    async def get(self, tenant_id: str, skill_id: uuid.UUID) -> Skill | None:
        skill: Skill | None = await self._session.scalar(
            select(Skill).where(
                Skill.id == skill_id,
                Skill.tenant_id == tenant_id,
                Skill.is_deleted.is_(False),
            )
        )
        return skill

    async def get_current_artifact(self, skill: Skill) -> SkillArtifact | None:
        if skill.current_artifact_id is None:
            return None
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.id == skill.current_artifact_id,
                SkillArtifact.is_deleted.is_(False),
            )
        )
        return artifact

    async def find_by_key(self, tenant_id: str, key: str) -> Skill | None:
        skill: Skill | None = await self._session.scalar(
            select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.key == key,
                Skill.is_deleted.is_(False),
            )
        )
        return skill

    async def add(self, skill: Skill) -> Skill:
        self._session.add(skill)
        await self._session.flush()
        return skill

    async def list_storage_keys(self) -> set[str]:
        rows = await self._session.execute(select(SkillArtifact.storage_key))
        return {row[0] for row in rows.all()}

    async def list_artifacts(
        self,
        skill_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[SkillArtifact], int]:
        conditions = (
            SkillArtifact.skill_id == skill_id,
            SkillArtifact.is_deleted.is_(False),
        )
        total = await self._session.scalar(
            select(func.count()).select_from(SkillArtifact).where(*conditions)
        )
        result = await self._session.scalars(
            select(SkillArtifact)
            .where(*conditions)
            .order_by(SkillArtifact.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.all()), int(total or 0)

    async def get_artifact(
        self,
        skill_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> SkillArtifact | None:
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.id == artifact_id,
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.is_deleted.is_(False),
            )
        )
        return artifact

    async def find_artifact_by_version(
        self,
        skill_id: uuid.UUID,
        version: str,
    ) -> SkillArtifact | None:
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.version == version,
                SkillArtifact.is_deleted.is_(False),
            )
        )
        return artifact

    async def find_artifact_by_checksum(
        self,
        skill_id: uuid.UUID,
        checksum: str,
    ) -> SkillArtifact | None:
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.checksum == checksum,
                SkillArtifact.is_deleted.is_(False),
            )
        )
        return artifact

    async def count_agent_bindings(self, skill_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentSkillBinding)
            .join(Skill, Skill.id == AgentSkillBinding.skill_id)
            .where(
                AgentSkillBinding.skill_id == skill_id,
                AgentSkillBinding.is_deleted.is_(False),
                Skill.is_deleted.is_(False),
            )
        )
        return int(total or 0)

    async def list_effective_for_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[tuple[Skill, SkillArtifact]]:
        granted_skill_ids = select(SkillUserGrant.skill_id).where(
            SkillUserGrant.user_id == user_id,
            SkillUserGrant.is_deleted.is_(False),
        )
        result = await self._session.execute(
            select(Skill, SkillArtifact)
            .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
            .join(SkillArtifact, current_artifact_join())
            .where(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.is_deleted.is_(False),
                Skill.tenant_id == tenant_id,
                Skill.enabled.is_(True),
                Skill.is_deleted.is_(False),
                or_(
                    Skill.user_scope == USER_SCOPE_ALL,
                    Skill.id.in_(granted_skill_ids),
                ),
            )
            .order_by(AgentSkillBinding.sort_order, Skill.key)
        )
        return [(skill, artifact) for skill, artifact in result.all()]
