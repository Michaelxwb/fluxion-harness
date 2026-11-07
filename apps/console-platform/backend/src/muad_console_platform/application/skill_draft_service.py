"""用户 Skill 草稿应用服务：文件读写、校验、打包提交与状态查询。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import SkillReviewStatus, SkillSource
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import (
    Skill,
    SkillArtifact,
    SkillDraft,
    SkillImportIdempotency,
)
from ..infrastructure.repositories.skill_draft_repository import SkillDraftRepository
from ..infrastructure.repositories.skill_repository import SkillRepository
from ..infrastructure.skill_artifact_store import (
    artifact_storage_key,
    remove_artifact,
    write_artifact,
)
from ..infrastructure.skill_draft_store import (
    DraftFile,
    SkillDraftStore,
    normalize_relative_path,
    validate_draft_key,
)
from ..infrastructure.skill_validator import (
    ValidatedSkillPackage,
    checksum_of,
    invalid_package,
    review_package,
)
from .audit_service import AuditActor, AuditService
from .skill_service import skill_snapshot

AUDIT_DRAFT = "SKILL_DRAFT"
AUDIT_ARTIFACT = "SKILL_ARTIFACT"
AUDIT_SKILL = "SKILL"
ENDPOINT_SUBMIT = "draft_submit"
VALIDATION_READY = "READY"


def draft_view(draft: SkillDraft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "tenant_id": draft.tenant_id,
        "owner_user_id": str(draft.owner_user_id),
        "key": draft.key,
        "revision": draft.revision,
        "last_submitted_artifact_id": (
            str(draft.last_submitted_artifact_id) if draft.last_submitted_artifact_id else None
        ),
        "last_submitted_revision": draft.last_submitted_revision,
        "file_count": draft.file_count,
        "total_size": draft.total_size,
        "create_time": draft.create_time.isoformat() if draft.create_time else None,
        "update_time": draft.update_time.isoformat() if draft.update_time else None,
    }


def submission_view(artifact: SkillArtifact) -> dict[str, Any]:
    return {
        "skill_id": str(artifact.skill_id),
        "artifact_id": str(artifact.id),
        "review_status": artifact.review_status,
        "version": artifact.version,
        "checksum": artifact.checksum,
    }


@dataclass(frozen=True, slots=True)
class DraftChange:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DraftApplyResult:
    revision: int
    file_count: int
    total_size: int
    changed_files: tuple[str, ...]
    deleted_files: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "changed_files": list(self.changed_files),
            "deleted_files": list(self.deleted_files),
        }


class SkillDraftService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._drafts = SkillDraftRepository(session)
        self._skills = SkillRepository(session)
        self._store = SkillDraftStore.from_settings()
        self._audit = AuditService(session)

    async def create(
        self, tenant_id: str, owner_user_id: uuid.UUID, key: str
    ) -> dict[str, Any]:
        validate_draft_key(key)
        existing = await self._drafts.find_by_key(tenant_id, owner_user_id, key)
        if existing is not None:
            self._store.ensure(existing)
            return draft_view(existing)
        draft = await self._insert_draft(tenant_id, owner_user_id, key)
        self._store.ensure(draft)
        await self._audit_draft(tenant_id, owner_user_id, "CREATE", draft, {"key": draft.key})
        return draft_view(draft)

    async def _insert_draft(
        self, tenant_id: str, owner_user_id: uuid.UUID, key: str
    ) -> SkillDraft:
        try:
            draft = await self._drafts.create(tenant_id, owner_user_id, key)
            await self._session.flush()
            return draft
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._drafts.find_by_key(tenant_id, owner_user_id, key)
            if existing is None:
                raise AppError(ErrorCode.COMMON_CONFLICT) from exc
            return existing

    async def metadata(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> dict[str, Any]:
        draft = await self._require(tenant_id, owner_user_id, draft_id)
        return draft_view(draft)

    async def archive(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> tuple[dict[str, Any], bytes]:
        """草稿 Shell 取件：zip 与 revision 必须在同一响应里返回，避免取件/执行间漂移。"""
        draft = await self._require(tenant_id, owner_user_id, draft_id)
        return draft_view(draft), self._store.export_zip(draft)

    async def read(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        path: str,
    ) -> dict[str, Any]:
        draft = await self._require(tenant_id, owner_user_id, draft_id)
        result = self._store.read(draft, path)
        if isinstance(result, DraftFile):
            return {
                "type": "file",
                "path": result.path,
                "content": result.content,
                "encoding": result.encoding,
                "size": result.size,
            }
        return result

    async def write(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        path: str,
        content: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        draft = await self._require_for_update(tenant_id, owner_user_id, draft_id)
        if draft.revision != expected_revision:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        relative = normalize_relative_path(path)
        new_size = len(content.encode("utf-8"))
        old_size = self._store.existing_size(draft, path)
        file_count = draft.file_count + (0 if old_size is not None else 1)
        total_size = draft.total_size - (old_size or 0) + new_size
        self._enforce_quota(file_count=file_count, total_size=total_size, path=relative.as_posix())
        advanced = await self._drafts.advance_revision(
            tenant_id,
            owner_user_id,
            draft_id,
            expected_revision=expected_revision,
            file_count=file_count,
            total_size=total_size,
        )
        if advanced is None:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        self._store.write(draft, relative.as_posix(), content)
        await self._audit_draft(
            tenant_id,
            owner_user_id,
            "UPDATE",
            advanced,
            {"path": relative.as_posix(), "size": new_size},
        )
        return {
            "path": relative.as_posix(),
            "size": new_size,
            "revision": advanced.revision,
            "file_count": advanced.file_count,
            "total_size": advanced.total_size,
        }

    async def apply_changes(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        expected_revision: int,
        changes: Sequence[DraftChange],
        deletions: Sequence[str],
    ) -> dict[str, Any]:
        """草稿 Shell 一次性回写：所有变更在同一个 revision CAS 内提交。

        先校验全部路径与配额，再推进 revision，最后落盘；任一校验失败不会推进 revision。
        """
        draft = await self._require_for_update(tenant_id, owner_user_id, draft_id)
        if draft.revision != expected_revision:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        file_count = draft.file_count
        total_size = draft.total_size
        normalized_changes: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        for change in changes:
            relative = normalize_relative_path(change.path)
            key = relative.as_posix()
            if key in seen:
                raise AppError(ErrorCode.DRAFT_PATH_INVALID, data={"path": key})
            seen.add(key)
            if len(change.content) > self._store.file_max_bytes:
                raise AppError(
                    ErrorCode.DRAFT_QUOTA_EXCEEDED,
                    data={"reason": "FILE_TOO_LARGE", "path": key},
                )
            old_size = self._store.existing_size(draft, key)
            file_count += 0 if old_size is not None else 1
            total_size += len(change.content) - (old_size or 0)
            normalized_changes.append((key, change.content))
        normalized_deletions: list[str] = []
        for raw_path in deletions:
            relative = normalize_relative_path(raw_path)
            key = relative.as_posix()
            if key in seen:
                raise AppError(ErrorCode.DRAFT_PATH_INVALID, data={"path": key})
            seen.add(key)
            old_size = self._store.existing_size(draft, key)
            if old_size is None:
                raise AppError(ErrorCode.DRAFT_NOT_FOUND)
            file_count -= 1
            total_size -= old_size
            normalized_deletions.append(key)
        first_path = ""
        if normalized_changes:
            first_path = normalized_changes[0][0]
        elif normalized_deletions:
            first_path = normalized_deletions[0]
        self._enforce_quota(
            file_count=file_count,
            total_size=total_size,
            path=first_path,
        )
        advanced = await self._drafts.advance_revision(
            tenant_id,
            owner_user_id,
            draft_id,
            expected_revision=expected_revision,
            file_count=file_count,
            total_size=total_size,
        )
        if advanced is None:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        for key, data in normalized_changes:
            self._store.write_bytes(draft, key, data)
        for key in normalized_deletions:
            self._store.remove_file(draft, key)
        result = DraftApplyResult(
            revision=advanced.revision,
            file_count=advanced.file_count,
            total_size=advanced.total_size,
            changed_files=tuple(key for key, _ in normalized_changes),
            deleted_files=tuple(normalized_deletions),
        )
        await self._audit_draft(
            tenant_id,
            owner_user_id,
            "SHELL_APPLY",
            advanced,
            {
                "changed_files": list(result.changed_files),
                "deleted_files": list(result.deleted_files),
                "file_count": result.file_count,
                "total_size": result.total_size,
            },
        )
        return result.as_dict()

    async def delete_file(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        path: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        draft = await self._require_for_update(tenant_id, owner_user_id, draft_id)
        if expected_revision is not None and draft.revision != expected_revision:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        relative = normalize_relative_path(path)
        size = self._store.existing_size(draft, path)
        if size is None:
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        file_count = max(0, draft.file_count - 1)
        total_size = max(0, draft.total_size - size)
        advanced = await self._drafts.advance_revision(
            tenant_id,
            owner_user_id,
            draft_id,
            expected_revision=draft.revision,
            file_count=file_count,
            total_size=total_size,
        )
        if advanced is None:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        self._store.remove_file(draft, relative.as_posix())
        await self._audit_draft(
            tenant_id,
            owner_user_id,
            "DELETE",
            advanced,
            {"path": relative.as_posix(), "size": size},
        )
        return {
            "path": relative.as_posix(),
            "deleted": True,
            "revision": advanced.revision,
            "file_count": advanced.file_count,
            "total_size": advanced.total_size,
        }

    async def delete_draft(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> dict[str, Any]:
        draft = await self._require_for_update(tenant_id, owner_user_id, draft_id)
        self._store.remove_draft(draft)
        deleted = await self._drafts.soft_delete(tenant_id, owner_user_id, draft_id)
        if not deleted:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        await self._audit_draft(
            tenant_id,
            owner_user_id,
            "DELETE",
            draft,
            {"key": draft.key, "deleted": True},
        )
        return {"id": str(draft_id), "deleted": True}

    async def review(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        default_script: str | None = None,
    ) -> dict[str, Any]:
        draft = await self._require(tenant_id, owner_user_id, draft_id)
        data = self._store.export_zip(draft)
        review = review_package(data, default_script=default_script)
        return {"draft_id": str(draft.id), "revision": draft.revision, **review.as_dict()}

    async def submit(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        expected_revision: int,
        version: str | None = None,
        default_script: str | None = None,
        submitted_agent_id: uuid.UUID | None = None,
        submitted_channel: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(
            ENDPOINT_SUBMIT,
            tenant_id,
            {
                "draft_id": str(draft_id),
                "owner_user_id": str(owner_user_id),
                "expected_revision": expected_revision,
                "version": version,
                "default_script": default_script,
                "submitted_agent_id": str(submitted_agent_id) if submitted_agent_id else None,
                "submitted_channel": submitted_channel,
            },
        )
        if idempotency_key:
            await self._lock_idempotency(tenant_id, idempotency_key, ENDPOINT_SUBMIT)
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, ENDPOINT_SUBMIT, fingerprint
            )
            if replayed is not None:
                return replayed

        draft = await self._require_for_update(tenant_id, owner_user_id, draft_id)
        if draft.revision != expected_revision:
            raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
        replay = await self._replay_pending(draft, expected_revision, version)
        if replay is not None:
            if idempotency_key:
                await self._record_idempotency(
                    tenant_id, idempotency_key, ENDPOINT_SUBMIT, fingerprint, replay
                )
            return replay

        data = self._store.export_zip(draft)
        review = review_package(data, default_script=default_script)
        if review.status == "error" or review.package is None:
            failure = next((check for check in review.checks if check.status == "error"), None)
            raise invalid_package(
                failure.code if failure else "INVALID_PACKAGE",
                **({"path": failure.path} if failure and failure.path else {}),
            )
        package = review.package
        chosen_version = version or f"0.0.{draft.revision}"
        checksum = checksum_of(data)
        skill = await self._ensure_skill(tenant_id, draft, package, chosen_version)
        artifact = await self._create_pending_artifact(
            draft,
            skill,
            package=package,
            data=data,
            checksum=checksum,
            version=chosen_version,
            default_script=default_script,
            submitted_agent_id=submitted_agent_id,
            submitted_channel=submitted_channel,
        )
        result = submission_view(artifact)
        try:
            stored = await self._drafts.mark_submitted(
                tenant_id,
                owner_user_id,
                draft_id,
                revision=expected_revision,
                artifact_id=artifact.id,
            )
            if stored is None:
                raise AppError(ErrorCode.DRAFT_REVISION_CONFLICT)
            await self._record_submit_audit(tenant_id, owner_user_id, skill, artifact, stored)
            if idempotency_key:
                await self._record_idempotency(
                    tenant_id, idempotency_key, ENDPOINT_SUBMIT, fingerprint, result
                )
        except BaseException:
            remove_artifact(artifact.storage_key)
            raise
        return result

    async def status(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> dict[str, Any]:
        draft = await self._require(tenant_id, owner_user_id, draft_id)
        artifact = await self._last_artifact(draft)
        result = {
            "draft_id": str(draft.id),
            "revision": draft.revision,
            "last_submitted_revision": draft.last_submitted_revision,
            "skill_id": None,
            "artifact_id": None,
            "version": None,
            "checksum": None,
            "review_status": None,
            "review_note": None,
            "reviewed_at": None,
            "submitted_at": None,
        }
        if artifact is None:
            return result
        result.update(
            {
                "skill_id": str(artifact.skill_id),
                "artifact_id": str(artifact.id),
                "version": artifact.version,
                "checksum": artifact.checksum,
                "review_status": artifact.review_status,
                "review_note": artifact.review_note,
                "reviewed_at": artifact.reviewed_at.isoformat() if artifact.reviewed_at else None,
                "submitted_at": artifact.create_time.isoformat() if artifact.create_time else None,
            }
        )
        return result

    async def _replay_pending(
        self,
        draft: SkillDraft,
        expected_revision: int,
        requested_version: str | None,
    ) -> dict[str, Any] | None:
        if draft.last_submitted_revision != expected_revision or draft.last_submitted_artifact_id is None:
            return None
        artifact = await self._last_artifact(draft)
        if artifact is None:
            return None
        if artifact.review_status == SkillReviewStatus.PENDING:
            if requested_version is not None and requested_version != artifact.version:
                raise AppError(ErrorCode.SKILL_PENDING_EXISTS)
            return submission_view(artifact)
        if artifact.review_status == SkillReviewStatus.APPROVED:
            raise AppError(ErrorCode.SKILL_APPROVED_LOCKED)
        return None

    async def _ensure_skill(
        self,
        tenant_id: str,
        draft: SkillDraft,
        package: ValidatedSkillPackage,
        version: str,
    ) -> Skill:
        skill = await self._skills.find_by_key_for_update(tenant_id, draft.key)
        if skill is None:
            skill = Skill(
                tenant_id=tenant_id,
                key=draft.key,
                name=package.manifest.name,
                description=package.manifest.description,
                platform_label=package.manifest.platform_label,
                user_scope="SELECTED",
                enabled=True,
                source=SkillSource.USER,
                author=package.author,
                owner_user_id=draft.owner_user_id,
            )
            try:
                await self._skills.add(skill)
            except IntegrityError as exc:
                await self._session.rollback()
                raise AppError(ErrorCode.SKILL_KEY_EXISTS, message_args={"key": draft.key}) from exc
            await self._record_skill_audit(tenant_id, draft.owner_user_id, "CREATE", skill, None)
            return skill
        if skill.owner_user_id != draft.owner_user_id or skill.source != SkillSource.USER:
            raise AppError(ErrorCode.SKILL_KEY_EXISTS, message_args={"key": draft.key})
        current = await self._skills.get_current_artifact(skill)
        if current is not None and current.review_status == SkillReviewStatus.APPROVED:
            raise AppError(ErrorCode.SKILL_APPROVED_LOCKED)
        pending = await self._skills.find_pending(skill.id)
        if pending is not None:
            if pending.version == version:
                raise AppError(ErrorCode.SKILL_VERSION_EXISTS)
            pending.review_status = SkillReviewStatus.SUPERSEDED
            pending.update_time = datetime.now(UTC)
            await self._session.flush()
        before = skill_snapshot(skill)
        skill.name = package.manifest.name
        skill.description = package.manifest.description
        skill.platform_label = package.manifest.platform_label
        skill.author = package.author or skill.author
        skill.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_skill_audit(tenant_id, draft.owner_user_id, "UPDATE", skill, before)
        return skill

    async def _create_pending_artifact(
        self,
        draft: SkillDraft,
        skill: Skill,
        *,
        package: ValidatedSkillPackage,
        data: bytes,
        checksum: str,
        version: str,
        default_script: str | None,
        submitted_agent_id: uuid.UUID | None,
        submitted_channel: str | None,
    ) -> SkillArtifact:
        artifact_id = uuid.uuid4()
        storage_key = artifact_storage_key(skill.id, artifact_id)
        artifact = SkillArtifact(
            id=artifact_id,
            skill_id=skill.id,
            version=version,
            checksum=checksum,
            storage_key=storage_key,
            frontmatter_json=package.frontmatter,
            manifest_json={
                "files": list(package.files),
                "file_count": len(package.files),
                "total_size": package.total_size,
            },
            execution_mode=package.manifest.execution.value,
            instructions=package.instructions,
            default_script=default_script,
            package_size=len(data),
            validation_status=VALIDATION_READY,
            created_by=draft.owner_user_id,
            review_status=SkillReviewStatus.PENDING,
            source=SkillSource.USER,
            submitted_by_user_id=draft.owner_user_id,
            submitted_agent_id=submitted_agent_id,
            submitted_channel=submitted_channel,
        )
        write_artifact(storage_key, data)
        try:
            self._session.add(artifact)
            await self._session.flush()
            await self._record_artifact_audit(draft.tenant_id, draft.owner_user_id, "CREATE", artifact)
        except IntegrityError as exc:
            remove_artifact(storage_key)
            raise AppError(ErrorCode.SKILL_PENDING_EXISTS) from exc
        except BaseException:
            remove_artifact(storage_key)
            raise
        return artifact

    async def _last_artifact(self, draft: SkillDraft) -> SkillArtifact | None:
        if draft.last_submitted_artifact_id is None:
            return None
        artifact: SkillArtifact | None = await self._session.scalar(
            select(SkillArtifact).where(
                SkillArtifact.id == draft.last_submitted_artifact_id,
                SkillArtifact.is_deleted.is_(False),
            )
        )
        return artifact

    def _enforce_quota(self, *, file_count: int, total_size: int, path: str) -> None:
        if file_count > self._store.file_limit or total_size > self._store.quota_bytes:
            raise AppError(
                ErrorCode.DRAFT_QUOTA_EXCEEDED,
                data={"path": path, "file_count": file_count, "total_size": total_size},
            )

    async def _require(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> SkillDraft:
        draft = await self._drafts.get(tenant_id, owner_user_id, draft_id)
        if draft is None:
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        return draft

    async def _require_for_update(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> SkillDraft:
        draft = await self._drafts.get_for_update(tenant_id, owner_user_id, draft_id)
        if draft is None:
            raise AppError(ErrorCode.DRAFT_NOT_FOUND)
        return draft

    def _fingerprint(self, endpoint: str, tenant_id: str, fields: dict[str, Any]) -> str:
        canonical = json.dumps(
            {**fields, "endpoint": endpoint, "tenant_id": tenant_id},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return checksum_of(canonical.encode("utf-8"))

    async def _lock_idempotency(
        self, tenant_id: str, idempotency_key: str, endpoint: str
    ) -> None:
        digest = hashlib.sha256(f"{tenant_id}|{idempotency_key}|{endpoint}".encode()).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _idempotency_replay(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        record = await self._skills.find_idempotency(tenant_id, idempotency_key, endpoint)
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return dict(record.response_json)

    async def _record_idempotency(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        self._session.add(
            SkillImportIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_fingerprint=fingerprint,
                response_json=response,
            )
        )
        await self._session.flush()

    async def _audit_draft(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        action: str,
        draft: SkillDraft,
        after: dict[str, Any],
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=AuditActor(account_id=owner_user_id),
            resource_type=AUDIT_DRAFT,
            resource_id=draft.id,
            action=action,
            before=None,
            after={
                "draft_id": str(draft.id),
                "key": draft.key,
                "revision": draft.revision,
                "file_count": draft.file_count,
                "total_size": draft.total_size,
                **after,
            },
        )

    async def _record_skill_audit(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        action: str,
        skill: Skill,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=AuditActor(account_id=owner_user_id),
            resource_type=AUDIT_SKILL,
            resource_id=skill.id,
            action=action,
            before=before,
            after=skill_snapshot(skill),
        )

    async def _record_artifact_audit(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        action: str,
        artifact: SkillArtifact,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=AuditActor(account_id=owner_user_id),
            resource_type=AUDIT_ARTIFACT,
            resource_id=artifact.id,
            action=action,
            before=None,
            after={
                "skill_id": str(artifact.skill_id),
                "version": artifact.version,
                "checksum": artifact.checksum,
                "review_status": artifact.review_status,
                "submitted_by_user_id": str(artifact.submitted_by_user_id)
                if artifact.submitted_by_user_id
                else None,
                "submitted_agent_id": str(artifact.submitted_agent_id)
                if artifact.submitted_agent_id
                else None,
                "submitted_channel": artifact.submitted_channel,
            },
        )

    async def _record_submit_audit(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        skill: Skill,
        artifact: SkillArtifact,
        draft: SkillDraft,
    ) -> None:
        await self._audit_draft(
            tenant_id,
            owner_user_id,
            "SUBMIT",
            draft,
            {"artifact_id": str(artifact.id), "version": artifact.version},
        )
        await self._record_skill_audit(tenant_id, owner_user_id, "SUBMIT", skill, None)
