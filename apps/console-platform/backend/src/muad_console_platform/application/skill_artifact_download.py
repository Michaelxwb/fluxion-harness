"""管理员下载 Skill artifact：归属/checksum 校验、审计与响应元信息。

下载是只读操作，但会泄露用户包内容，因此必须写 `SKILL_ARTIFACT_DOWNLOAD` 审计；校验
失败时只留下告警，不把文件字节交给调用方（design §3.4 API-06 / §3.5 RISK-07）。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import Skill, SkillArtifact
from ..infrastructure.repositories.skill_repository import SkillRepository
from ..infrastructure.skill_artifact_store import artifact_path
from .audit_service import AuditActor, AuditService
from .skill_audit import SkillAuditAction, SkillAuditEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillArtifactDownload:
    """下载响应的非敏感元信息；`storage_key` 不出现在这里。"""

    path: Path
    checksum: str
    filename: str


class SkillArtifactDownloadService:
    def __init__(self, session: AsyncSession) -> None:
        self._skills = SkillRepository(session)
        self._audit = AuditService(session)

    async def download(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        artifact_id: uuid.UUID,
        actor: AuditActor,
    ) -> SkillArtifactDownload:
        skill = await self._skills.get(tenant_id, skill_id)
        if skill is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        artifact = await self._skills.get_artifact(skill.id, artifact_id)
        if artifact is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

        path = artifact_path(artifact.storage_key)
        self._verify_checksum(skill, artifact, path)
        await self._audit.record_skill_event(
            SkillAuditEvent(
                tenant_id=tenant_id,
                actor_id=actor.account_id,
                action=SkillAuditAction.ARTIFACT_DOWNLOAD,
                skill_id=skill.id,
                artifact_id=artifact.id,
                result="SUCCESS",
                detail={
                    "review_status": artifact.review_status,
                    "checksum": artifact.checksum,
                },
                source_ip=actor.source_ip,
            )
        )
        return SkillArtifactDownload(
            path=path,
            checksum=artifact.checksum,
            filename=_download_filename(skill, artifact),
        )

    def _verify_checksum(
        self, skill: Skill, artifact: SkillArtifact, path: Path
    ) -> None:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            logger.warning(
                "skill_artifact_unavailable skill_id=%s artifact_id=%s",
                skill.id,
                artifact.id,
            )
            raise AppError(ErrorCode.SKILL_ARTIFACT_UNAVAILABLE) from exc
        if f"sha256:{digest.hexdigest()}" != artifact.checksum:
            logger.warning(
                "skill_artifact_integrity_error skill_id=%s artifact_id=%s",
                skill.id,
                artifact.id,
            )
            raise AppError(ErrorCode.SKILL_ARTIFACT_INTEGRITY_ERROR)


def _download_filename(skill: Skill, artifact: SkillArtifact) -> str:
    version = artifact.version.replace("/", "_").replace("\\", "_")
    return f"{skill.key}-{version}.zip"
