"""用户 Skill 审批应用服务：行锁事务、状态机、自动授权/绑定与审计。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import SkillReviewStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import Skill, SkillArtifact
from ..infrastructure.repositories.agent_access_grant_repository import (
    AgentAccessGrantRepository,
)
from ..infrastructure.repositories.skill_repository import SkillRepository
from ..metrics import SkillReviewAction, record_skill_review
from .audit_service import AuditActor, AuditService
from .dto import SkillDetail
from .skill_audit import SkillAuditAction, SkillAuditEvent
from .skill_service import SkillService

ACTION_APPROVE = "APPROVE"
ACTION_REJECT = "REJECT"


class SkillReviewService:
    """审批/驳回：锁 Skill + artifact 行后复核状态，再在同一事务内授权、绑定并审计。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._skills = SkillRepository(session)
        self._access = AgentAccessGrantRepository(session)
        self._service = SkillService(session)
        self._audit = AuditService(session)

    async def review(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        *,
        artifact_id: uuid.UUID,
        action: str,
        review_note: str | None,
        actor: AuditActor,
    ) -> SkillDetail:
        if action not in (ACTION_APPROVE, ACTION_REJECT):
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        skill = await self._locked_skill(tenant_id, skill_id)
        artifact = await self._locked_pending_artifact(skill.id, artifact_id)
        if action == ACTION_REJECT:
            self._reject(artifact, review_note, actor)
        else:
            await self._approve(tenant_id, skill, artifact, actor, review_note)
        await self._audit_review(tenant_id, skill, artifact, action, actor)
        record_skill_review(cast(SkillReviewAction, action))
        return await self._service.get_skill_detail(tenant_id, skill.id)

    async def _locked_skill(self, tenant_id: str, skill_id: uuid.UUID) -> Skill:
        skill = await self._skills.get_for_update(tenant_id, skill_id)
        if skill is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return skill

    async def _locked_pending_artifact(
        self, skill_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> SkillArtifact:
        artifact = await self._skills.get_artifact_for_update(skill_id, artifact_id)
        if artifact is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if artifact.review_status != SkillReviewStatus.PENDING:
            raise AppError(ErrorCode.SKILL_ARTIFACT_NOT_PENDING)
        return artifact

    def _reject(
        self,
        artifact: SkillArtifact,
        review_note: str | None,
        actor: AuditActor,
    ) -> None:
        note = (review_note or "").strip()
        if not note:
            raise AppError(
                ErrorCode.COMMON_VALIDATION_ERROR,
                data={"field": "review_note"},
            )
        artifact.review_status = SkillReviewStatus.REJECTED
        artifact.review_note = note
        artifact.reviewed_by = actor.account_id
        artifact.reviewed_at = datetime.now(UTC)

    async def _approve(
        self,
        tenant_id: str,
        skill: Skill,
        artifact: SkillArtifact,
        actor: AuditActor,
        review_note: str | None,
    ) -> None:
        submitter_id = artifact.submitted_by_user_id or skill.owner_user_id
        await self._require_agent_access(tenant_id, artifact, submitter_id)
        artifact.review_status = SkillReviewStatus.APPROVED
        artifact.review_note = (review_note or "").strip() or None
        artifact.reviewed_by = actor.account_id
        artifact.reviewed_at = datetime.now(UTC)
        skill.current_artifact_id = artifact.id
        skill.update_time = datetime.now(UTC)
        await self._session.flush()
        if submitter_id is not None:
            await self._service.add_grant(tenant_id, skill.id, submitter_id, actor)
        if artifact.submitted_agent_id is not None:
            await self._service.bind_skill(
                tenant_id, artifact.submitted_agent_id, skill.id, actor
            )

    async def _require_agent_access(
        self,
        tenant_id: str,
        artifact: SkillArtifact,
        submitter_id: uuid.UUID | None,
    ) -> None:
        agent_id = artifact.submitted_agent_id
        if agent_id is None:
            return
        if submitter_id is None or not await self._access.has_active_grant(
            tenant_id, submitter_id, agent_id
        ):
            raise AppError(ErrorCode.AGENT_ACCESS_REVOKED)

    async def _audit_review(
        self,
        tenant_id: str,
        skill: Skill,
        artifact: SkillArtifact,
        action: str,
        actor: AuditActor,
    ) -> None:
        await self._audit.record_skill_event(
            SkillAuditEvent(
                tenant_id=tenant_id,
                actor_id=actor.account_id,
                action=SkillAuditAction.REVIEW,
                skill_id=skill.id,
                artifact_id=artifact.id,
                agent_id=artifact.submitted_agent_id,
                result=action,
                detail={
                    "review_status": artifact.review_status,
                    "submitted_by_user_id": (
                        str(artifact.submitted_by_user_id)
                        if artifact.submitted_by_user_id
                        else None
                    ),
                },
                source_ip=actor.source_ip,
            )
        )
