import uuid

from sqlalchemy import ColumnElement, ScalarSelect, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import (
    AgentDefinition,
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillImportIdempotency,
    SkillUserGrant,
)

USER_SCOPE_ALL = "ALL"
SKILL_SOURCE_SYSTEM = "SYSTEM"


def current_artifact_join() -> ColumnElement[bool]:
    return and_(
        SkillArtifact.id == Skill.current_artifact_id,
        SkillArtifact.is_deleted.is_(False),
    )


def effective_binding_join(agent_id: uuid.UUID) -> ColumnElement[bool]:
    """Effective 查询的 Agent→Skill 绑定腿：只匹配当前 Agent 的存活绑定。

    绑定放在 JOIN 谓词里（而不是 WHERE），系统 Skill 才能在无绑定时仍保留一行；
    同一 Agent+Skill 的 partial unique 保证不会因为多绑定行放大结果。
    """
    return and_(
        AgentSkillBinding.skill_id == Skill.id,
        AgentSkillBinding.agent_id == agent_id,
        AgentSkillBinding.is_deleted.is_(False),
    )


def latest_artifact_status_subquery() -> ScalarSelect[str]:
    """最近一次提交的审批状态：待审/驳回包不是 current artifact，但列表必须可见。"""
    return (
        select(SkillArtifact.review_status)
        .where(
            SkillArtifact.skill_id == Skill.id,
            SkillArtifact.is_deleted.is_(False),
        )
        .order_by(SkillArtifact.create_time.desc(), SkillArtifact.id.desc())
        .limit(1)
        .correlate(Skill)
        .scalar_subquery()
    )


def _effective_conditions(
    tenant_id: str, agent_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[ColumnElement[bool], ...]:
    """Effective Skill 公式（单处定义，list/count/catalog/execution 共用）。

    系统 Skill（source=SYSTEM）属于当前租户的全局能力，不依赖 AgentSkillBinding；
    普通 Skill 仍然要求当前 Agent 的存活绑定，再叠加 ALL/SELECTED 授权。两条腿都
    先经过 tenant/enabled/未软删过滤，普通 Skill 的授权条件不会被系统分支放大。
    """
    granted_skill_ids = select(SkillUserGrant.skill_id).where(
        SkillUserGrant.user_id == user_id,
        SkillUserGrant.is_deleted.is_(False),
    )
    bound_and_authorized = and_(
        AgentSkillBinding.agent_id == agent_id,
        AgentSkillBinding.is_deleted.is_(False),
        or_(
            Skill.user_scope == USER_SCOPE_ALL,
            Skill.id.in_(granted_skill_ids),
        ),
    )
    return (
        Skill.tenant_id == tenant_id,
        Skill.enabled.is_(True),
        Skill.is_deleted.is_(False),
        or_(Skill.source == SKILL_SOURCE_SYSTEM, bound_and_authorized),
    )


class SkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_skills(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        user_scope: str | None,
        enabled: bool | None,
        execution_mode: str | None,
        source: str | None = None,
        review_status: str | None = None,
    ) -> tuple[list[tuple[Skill, SkillArtifact | None, int, int, str | None]], int]:
        latest_status = latest_artifact_status_subquery()
        conditions = [Skill.tenant_id == tenant_id, Skill.is_deleted.is_(False)]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(or_(Skill.key.ilike(pattern), Skill.name.ilike(pattern)))
        if user_scope is not None:
            conditions.append(Skill.user_scope == user_scope)
        if enabled is not None:
            conditions.append(Skill.enabled.is_(enabled))
        if source is not None:
            conditions.append(Skill.source == source)
        if review_status is not None:
            conditions.append(latest_status == review_status)
        if execution_mode is not None:
            conditions.append(SkillArtifact.execution_mode == execution_mode)
        total = await self._session.scalar(
            select(func.count())
            .select_from(Skill)
            .outerjoin(SkillArtifact, current_artifact_join())
            .where(*conditions)
        )
        agent_count = (
            select(func.count())
            .select_from(AgentSkillBinding)
            .where(
                AgentSkillBinding.skill_id == Skill.id,
                AgentSkillBinding.is_deleted.is_(False),
            )
            .correlate(Skill)
            .scalar_subquery()
        )
        user_count = (
            select(func.count())
            .select_from(SkillUserGrant)
            .where(
                SkillUserGrant.skill_id == Skill.id,
                SkillUserGrant.is_deleted.is_(False),
            )
            .correlate(Skill)
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(Skill, SkillArtifact, agent_count, user_count, latest_status)
            .outerjoin(SkillArtifact, current_artifact_join())
            .where(*conditions)
            .order_by(Skill.update_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = result.all()
        items = [
            (skill, artifact, int(bound or 0), int(granted or 0), status)
            for skill, artifact, bound, granted, status in rows
        ]
        return items, int(total or 0)

    async def get(self, tenant_id: str, skill_id: uuid.UUID) -> Skill | None:
        skill: Skill | None = await self._session.scalar(
            select(Skill).where(
                Skill.id == skill_id,
                Skill.tenant_id == tenant_id,
                Skill.is_deleted.is_(False),
            )
        )
        return skill

    async def get_including_deleted(self, tenant_id: str, skill_id: uuid.UUID) -> Skill | None:
        """幂等删除要区分「已软删」与「不存在」：已删除仍返回行。"""
        skill: Skill | None = await self._session.scalar(
            select(Skill).where(
                Skill.id == skill_id,
                Skill.tenant_id == tenant_id,
            )
        )
        return skill

    async def get_for_update(self, tenant_id: str, skill_id: uuid.UUID) -> Skill | None:
        """审批/提交串行化：锁住 Skill 行，避免审批与覆盖提交互相踩状态。"""
        skill: Skill | None = await self._session.scalar(
            select(Skill)
            .where(
                Skill.id == skill_id,
                Skill.tenant_id == tenant_id,
                Skill.is_deleted.is_(False),
            )
            .with_for_update()
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

    async def find_by_key_for_update(self, tenant_id: str, key: str) -> Skill | None:
        skill: Skill | None = await self._session.scalar(
            select(Skill)
            .where(
                Skill.tenant_id == tenant_id,
                Skill.key == key,
                Skill.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return skill

    async def add(self, skill: Skill) -> Skill:
        self._session.add(skill)
        await self._session.flush()
        return skill

    async def find_idempotency(
        self, tenant_id: str, idempotency_key: str, endpoint: str
    ) -> SkillImportIdempotency | None:
        rows = await self._session.execute(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == idempotency_key,
                SkillImportIdempotency.endpoint == endpoint,
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        return rows.scalar_one_or_none()

    async def add_idempotency(self, record: SkillImportIdempotency) -> None:
        self._session.add(record)

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

    async def get_artifact_for_update(
        self,
        skill_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> SkillArtifact | None:
        """审批事务锁 artifact 行；并发审批在同一行上串行复核状态。"""
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact)
            .where(
                SkillArtifact.id == artifact_id,
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return artifact

    async def latest_review_status(self, skill_id: uuid.UUID) -> str | None:
        status: str | None = await self._session.scalar(
            select(SkillArtifact.review_status)
            .where(
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.is_deleted.is_(False),
            )
            .order_by(SkillArtifact.create_time.desc(), SkillArtifact.id.desc())
            .limit(1)
        )
        return status

    async def find_pending(self, skill_id: uuid.UUID) -> SkillArtifact | None:
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.skill_id == skill_id,
                SkillArtifact.review_status == "PENDING",
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

    async def list_agents_for_skill(
        self,
        skill_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[AgentDefinition, AgentSkillBinding]], int]:
        conditions = (
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.is_deleted.is_(False),
            AgentDefinition.is_deleted.is_(False),
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentSkillBinding)
            .join(AgentDefinition, AgentDefinition.id == AgentSkillBinding.agent_id)
            .where(*conditions)
        )
        rows = (
            await self._session.execute(
                select(AgentDefinition, AgentSkillBinding)
                .join(AgentDefinition, AgentDefinition.id == AgentSkillBinding.agent_id)
                .where(*conditions)
                .order_by(AgentSkillBinding.sort_order, AgentDefinition.key)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(agent, binding) for agent, binding in rows], int(total or 0)

    async def list_active_bindings_for_skill(
        self, skill_id: uuid.UUID
    ) -> list[tuple[AgentDefinition, AgentSkillBinding]]:
        """一键解绑读取全部 active 绑定（含 Agent 身份，供审计记录解绑列表）。"""
        conditions = (
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.is_deleted.is_(False),
            AgentDefinition.is_deleted.is_(False),
        )
        rows = (
            await self._session.execute(
                select(AgentDefinition, AgentSkillBinding)
                .join(AgentDefinition, AgentDefinition.id == AgentSkillBinding.agent_id)
                .where(*conditions)
                .order_by(AgentSkillBinding.sort_order, AgentDefinition.key)
            )
        ).all()
        return [(agent, binding) for agent, binding in rows]

    async def list_effective_for_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[tuple[Skill, SkillArtifact]]:
        statement = (
            select(Skill, SkillArtifact)
            .outerjoin(AgentSkillBinding, effective_binding_join(agent_id))
            .join(SkillArtifact, current_artifact_join())
            .where(*_effective_conditions(tenant_id, agent_id, user_id))
            .order_by(AgentSkillBinding.sort_order, Skill.key)
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        result = await self._session.execute(statement)
        return [(skill, artifact) for skill, artifact in result.all()]

    async def count_effective_for_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        """与 list_effective_for_agent 同一组谓词，用于分页 total（有界，不取回全部行）。"""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Skill)
            .outerjoin(AgentSkillBinding, effective_binding_join(agent_id))
            .join(SkillArtifact, current_artifact_join())
            .where(*_effective_conditions(tenant_id, agent_id, user_id))
        )
        return int(total or 0)
