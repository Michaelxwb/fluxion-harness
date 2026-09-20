import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import (
    AgentDefinition,
    AgentSkillBinding,
    PlatformUser,
    Skill,
    SkillArtifact,
    SkillImportIdempotency,
    SkillUserGrant,
)
from ..infrastructure.repositories.agent_repository import AgentRepository
from ..infrastructure.repositories.agent_skill_binding_repository import AgentSkillBindingRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from ..infrastructure.repositories.skill_repository import SkillRepository
from ..infrastructure.repositories.skill_user_grant_repository import SkillUserGrantRepository
from ..infrastructure.skill_artifact_store import (
    artifact_storage_key,
    cleanup_orphan_files,
    remove_artifact,
    write_artifact,
)
from ..infrastructure.skill_validator import (
    ValidatedSkillPackage,
    checksum_of,
    validated_package,
)
from .audit_service import AuditActor, AuditService, sanitize_payload
from .dto import (
    AgentSkillBindingItem,
    SkillArtifactDetail,
    SkillDetail,
    SkillListItem,
    SkillUpdateRequest,
    SkillUserGrantItem,
)

AUDIT_SKILL = "SKILL"
AUDIT_ARTIFACT = "SKILL_ARTIFACT"
AUDIT_GRANT = "SKILL_USER_GRANT"
AUDIT_BINDING = "AGENT_SKILL_BINDING"
USER_SCOPE_SELECTED = "SELECTED"
VALIDATION_READY = "READY"


def skill_snapshot(skill: Skill) -> dict[str, Any]:
    return {
        "key": skill.key,
        "name": skill.name,
        "description": skill.description,
        "platform_label": skill.platform_label,
        "user_scope": skill.user_scope,
        "enabled": skill.enabled,
        "current_artifact_id": str(skill.current_artifact_id) if skill.current_artifact_id else None,
    }


def artifact_snapshot(artifact: SkillArtifact) -> dict[str, Any]:
    return {
        "skill_id": str(artifact.skill_id),
        "version": artifact.version,
        "checksum": artifact.checksum,
        "storage_key": artifact.storage_key,
        "execution_mode": artifact.execution_mode,
        "default_script": artifact.default_script,
        "package_size": artifact.package_size,
        "validation_status": artifact.validation_status,
        "frontmatter": sanitize_payload(artifact.frontmatter_json),
        "manifest": sanitize_payload(artifact.manifest_json),
    }


def artifact_detail(artifact: SkillArtifact) -> SkillArtifactDetail:
    return SkillArtifactDetail(
        id=artifact.id,
        artifact_id=artifact.id,
        skill_id=artifact.skill_id,
        version=artifact.version,
        checksum=artifact.checksum,
        storage_key=artifact.storage_key,
        execution_mode=artifact.execution_mode,
        default_script=artifact.default_script,
        package_size=artifact.package_size,
        validation_status=artifact.validation_status,
        frontmatter=sanitize_payload(artifact.frontmatter_json),
        manifest=sanitize_payload(artifact.manifest_json),
        created_by=artifact.created_by,
        create_time=artifact.create_time,
    )


def skill_list_item(
    skill: Skill,
    artifact: SkillArtifact | None,
    *,
    agent_count: int = 0,
    user_count: int = 0,
) -> SkillListItem:
    return SkillListItem(
        id=skill.id,
        key=skill.key,
        name=skill.name,
        description=skill.description,
        platform_label=skill.platform_label,
        user_scope=skill.user_scope,
        enabled=skill.enabled,
        current_artifact_id=skill.current_artifact_id,
        current_version=artifact.version if artifact else None,
        execution_mode=artifact.execution_mode if artifact else None,
        agent_count=agent_count,
        user_count=user_count,
        update_time=skill.update_time,
    )


def grant_item(grant: SkillUserGrant, user: PlatformUser) -> SkillUserGrantItem:
    return SkillUserGrantItem(
        user_id=user.id,
        user_code=user.user_code,
        display_name=user.display_name,
        granted_by=grant.granted_by,
        create_time=grant.create_time,
    )


def grant_snapshot(grant: SkillUserGrant) -> dict[str, Any]:
    return {
        "skill_id": str(grant.skill_id),
        "user_id": str(grant.user_id),
        "granted_by": str(grant.granted_by),
        "is_deleted": grant.is_deleted,
    }


def binding_item(
    binding: AgentSkillBinding,
    skill: Skill,
    artifact: SkillArtifact | None,
) -> AgentSkillBindingItem:
    return AgentSkillBindingItem(
        skill_id=skill.id,
        key=skill.key,
        name=skill.name,
        description=skill.description,
        platform_label=skill.platform_label,
        user_scope=skill.user_scope,
        enabled=skill.enabled,
        current_artifact_version=artifact.version if artifact else None,
        execution_mode=artifact.execution_mode if artifact else None,
        sort_order=binding.sort_order,
        create_time=binding.create_time,
    )


def binding_snapshot(binding: AgentSkillBinding) -> dict[str, Any]:
    return {
        "agent_id": str(binding.agent_id),
        "skill_id": str(binding.skill_id),
        "sort_order": binding.sort_order,
        "is_deleted": binding.is_deleted,
    }


class SkillService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._skills = SkillRepository(session)
        self._grants = SkillUserGrantRepository(session)
        self._bindings = AgentSkillBindingRepository(session)
        self._platform_users = PlatformUserRepository(session)
        self._agents = AgentRepository(session)
        self._audit = AuditService(session)

    async def list_skills(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        user_scope: str | None = None,
        execution_mode: str | None = None,
    ) -> tuple[list[SkillListItem], int]:
        rows, total = await self._skills.list_skills(
            tenant_id,
            page,
            page_size,
            user_scope,
            execution_mode,
        )
        return [
            skill_list_item(skill, artifact, agent_count=bound, user_count=granted)
            for skill, artifact, bound, granted in rows
        ], total

    async def get_skill(self, tenant_id: str, skill_id: uuid.UUID) -> Skill:
        skill = await self._skills.get(tenant_id, skill_id)
        if skill is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return skill

    async def get_skill_detail(self, tenant_id: str, skill_id: uuid.UUID) -> SkillDetail:
        skill = await self.get_skill(tenant_id, skill_id)
        artifact = await self._skills.get_current_artifact(skill)
        return SkillDetail(
            **skill_list_item(
                skill,
                artifact,
                agent_count=await self._skills.count_agent_bindings(skill.id),
                user_count=await self._grants.count_active(skill.id),
            ).model_dump(),
            create_time=skill.create_time,
            current_artifact=artifact_detail(artifact) if artifact else None,
        )

    async def update_skill(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        payload: SkillUpdateRequest,
        actor: AuditActor,
    ) -> SkillDetail:
        skill = await self.get_skill(tenant_id, skill_id)
        before = skill_snapshot(skill)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(skill, field, value)
        skill.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, AUDIT_SKILL, skill.id, "UPDATE", before, skill)
        return await self.get_skill_detail(tenant_id, skill_id)

    async def set_user_scope(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        user_scope: str,
        actor: AuditActor,
    ) -> SkillDetail:
        skill = await self.get_skill(tenant_id, skill_id)
        if skill.user_scope == user_scope:
            return await self.get_skill_detail(tenant_id, skill_id)
        before = skill_snapshot(skill)
        skill.user_scope = user_scope
        skill.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, AUDIT_SKILL, skill.id, "UPDATE", before, skill)
        return await self.get_skill_detail(tenant_id, skill_id)

    def _fingerprint(self, *parts: str | None) -> str:
        return checksum_of("|".join(str(part) for part in parts).encode())

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

    async def import_skill(
        self,
        tenant_id: str,
        *,
        version: str,
        key: str | None,
        default_script: str | None,
        data: bytes,
        actor: AuditActor,
        idempotency_key: str | None = None,
    ) -> SkillDetail:
        fingerprint = self._fingerprint("import", version, key, default_script, checksum_of(data))
        if idempotency_key:
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, "import", fingerprint
            )
            if replayed is not None:
                return SkillDetail(**replayed)
        with validated_package(data) as package:
            skill, _ = await self._persist_imported_skill(
                tenant_id,
                version=version,
                key=key,
                default_script=default_script,
                data=data,
                package=package,
                actor=actor,
            )
        detail = await self.get_skill_detail(tenant_id, skill.id)
        if idempotency_key:
            await self._record_idempotency(
                tenant_id, idempotency_key, "import", fingerprint, detail.model_dump(mode="json")
            )
        return detail

    async def _persist_imported_skill(
        self,
        tenant_id: str,
        *,
        version: str,
        key: str | None,
        default_script: str | None,
        data: bytes,
        package: ValidatedSkillPackage,
        actor: AuditActor,
    ) -> tuple[Skill, SkillArtifact]:
        chosen_key = key or package.default_key
        if not chosen_key:
            raise AppError(ErrorCode.SKILL_PACKAGE_INVALID)
        checksum = checksum_of(data)
        existing = await self._skills.find_by_key(tenant_id, chosen_key)
        if existing is not None:
            await self._require_absent_version(existing.id, version, checksum)
            raise AppError(ErrorCode.SKILL_KEY_EXISTS, message_args={"key": chosen_key})
        skill_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        storage_key = artifact_storage_key(skill_id, artifact_id)
        skill = Skill(
            id=skill_id,
            tenant_id=tenant_id,
            key=chosen_key,
            name=package.manifest.name,
            description=package.manifest.description,
            platform_label=package.manifest.platform_label,
            user_scope=USER_SCOPE_SELECTED,
            enabled=True,
        )
        artifact = self._build_artifact(
            skill_id=skill_id,
            artifact_id=artifact_id,
            storage_key=storage_key,
            version=version,
            checksum=checksum,
            default_script=self._validated_default_script(default_script, package),
            data=data,
            package=package,
            actor=actor,
        )
        skill.current_artifact_id = artifact_id
        write_artifact(storage_key, data)
        try:
            await self._skills.add(skill)
            self._session.add(artifact)
            await self._session.flush()
            await self._record_audit(tenant_id, actor, AUDIT_SKILL, skill.id, "CREATE", None, skill)
            await self._record_artifact_audit(tenant_id, actor, "CREATE", artifact, None)
        except IntegrityError as exc:
            remove_artifact(storage_key)
            raise AppError(ErrorCode.SKILL_KEY_EXISTS, message_args={"key": chosen_key}) from exc
        except BaseException:
            remove_artifact(storage_key)
            raise
        return skill, artifact

    async def add_artifact(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        *,
        version: str,
        default_script: str | None,
        data: bytes,
        actor: AuditActor,
        idempotency_key: str | None = None,
    ) -> SkillArtifactDetail:
        fingerprint = self._fingerprint("artifact", str(skill_id), version, default_script, checksum_of(data))
        if idempotency_key:
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, "artifact", fingerprint
            )
            if replayed is not None:
                return SkillArtifactDetail(**replayed)
        skill = await self.get_skill(tenant_id, skill_id)
        with validated_package(data) as package:
            checksum = checksum_of(data)
            await self._require_absent_version(skill.id, version, checksum)
            artifact_id = uuid.uuid4()
            storage_key = artifact_storage_key(skill.id, artifact_id)
            artifact = self._build_artifact(
                skill_id=skill.id,
                artifact_id=artifact_id,
                storage_key=storage_key,
                version=version,
                checksum=checksum,
                default_script=self._validated_default_script(default_script, package),
                data=data,
                package=package,
                actor=actor,
            )
            before = skill_snapshot(skill)
            write_artifact(storage_key, data)
            try:
                self._session.add(artifact)
                await self._session.flush()
                skill.current_artifact_id = artifact.id
                skill.update_time = datetime.now(UTC)
                await self._session.flush()
                await self._record_artifact_audit(tenant_id, actor, "CREATE", artifact, None)
                await self._record_audit(tenant_id, actor, AUDIT_SKILL, skill.id, "UPDATE", before, skill)
            except IntegrityError as exc:
                remove_artifact(storage_key)
                raise AppError(ErrorCode.SKILL_VERSION_EXISTS) from exc
            except BaseException:
                remove_artifact(storage_key)
                raise
        detail = artifact_detail(artifact)
        if idempotency_key:
            await self._record_idempotency(
                tenant_id, idempotency_key, "artifact", fingerprint, detail.model_dump(mode="json")
            )
        return detail

    async def cleanup_orphan_artifacts(
        self,
        *,
        grace_seconds: float = 3600.0,
        root: str | Path | None = None,
    ) -> list[str]:
        """扫描 Artifact 目录，删除 DB 无记录且早于宽限期的孤儿文件。"""
        known = await self._skills.list_storage_keys()
        return cleanup_orphan_files(known, grace_seconds=grace_seconds, root=root)

    async def list_artifacts(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[SkillArtifactDetail], int]:
        skill = await self.get_skill(tenant_id, skill_id)
        artifacts, total = await self._skills.list_artifacts(skill.id, page, page_size)
        return [artifact_detail(artifact) for artifact in artifacts], total

    async def get_artifact(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> SkillArtifactDetail:
        skill = await self.get_skill(tenant_id, skill_id)
        artifact = await self._skills.get_artifact(skill.id, artifact_id)
        if artifact is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return artifact_detail(artifact)

    async def list_grants(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SkillUserGrantItem], int]:
        skill = await self.get_skill(tenant_id, skill_id)
        rows, total = await self._grants.list_with_users(skill.id, page, page_size)
        return [grant_item(grant, user) for grant, user in rows], total

    async def add_grant(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        user_id: uuid.UUID,
        actor: AuditActor,
    ) -> SkillUserGrantItem:
        skill = await self.get_skill(tenant_id, skill_id)
        user = await self._require_platform_user(tenant_id, user_id)
        grant = await self._grants.find(skill.id, user_id)
        if grant is None or grant.is_deleted:
            before = grant_snapshot(grant) if grant is not None else None
            if grant is None:
                grant = SkillUserGrant(
                    skill_id=skill.id,
                    user_id=user_id,
                    granted_by=actor.account_id,
                )
                await self._grants.add(grant)
            else:
                grant.is_deleted = False
                grant.granted_by = actor.account_id
                grant.update_time = datetime.now(UTC)
                await self._session.flush()
            await self._record_grant_audit(tenant_id, actor, grant, before)
        # 幂等：已授权时直接返回既有记录，不重复写审计（与用户↔Agent 授权口径一致）
        return grant_item(grant, user)

    async def remove_grant(
        self,
        tenant_id: str,
        skill_id: uuid.UUID,
        user_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        skill = await self.get_skill(tenant_id, skill_id)
        grant = await self._grants.find(skill.id, user_id)
        if grant is None or grant.is_deleted:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = grant_snapshot(grant)
        grant.is_deleted = True
        grant.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_grant_audit(tenant_id, actor, grant, before)

    async def list_agent_skills(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AgentSkillBindingItem], int]:
        await self._require_agent(tenant_id, agent_id)
        rows = await self._bindings.list_for_agent(tenant_id, agent_id)
        items = [binding_item(binding, skill, artifact) for binding, skill, artifact in rows]
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)

    async def bind_skill(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        actor: AuditActor,
        sort_order: int = 0,
    ) -> AgentSkillBindingItem:
        agent = await self._require_agent(tenant_id, agent_id)
        skill = await self.get_skill(tenant_id, skill_id)
        binding = await self._bindings.find(agent.id, skill.id)
        artifact = await self._skills.get_current_artifact(skill)
        if binding is not None and not binding.is_deleted:
            return binding_item(binding, skill, artifact)
        before = binding_snapshot(binding) if binding is not None else None
        if binding is None:
            binding = AgentSkillBinding(
                agent_id=agent.id, skill_id=skill.id, sort_order=sort_order
            )
            await self._bindings.add(binding)
        else:
            binding.is_deleted = False
            binding.sort_order = sort_order  # 恢复历史行时应用新排序
            binding.update_time = datetime.now(UTC)
            await self._session.flush()
        await self._record_binding_audit(tenant_id, actor, binding, before)
        return binding_item(binding, skill, artifact)

    async def unbind_skill(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        actor: AuditActor,
    ) -> dict[str, Any]:
        agent = await self._require_agent(tenant_id, agent_id)
        binding = await self._bindings.find(agent.id, skill_id)
        if binding is None or binding.is_deleted:
            # 幂等：关系不存在或已解除仍返回成功
            return {"agent_id": str(agent.id), "skill_id": str(skill_id), "is_deleted": True}
        before = binding_snapshot(binding)
        binding.is_deleted = True
        binding.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_binding_audit(tenant_id, actor, binding, before)
        return {"agent_id": str(agent.id), "skill_id": str(skill_id), "is_deleted": True}

    def _build_artifact(
        self,
        *,
        skill_id: uuid.UUID,
        artifact_id: uuid.UUID,
        storage_key: str,
        version: str,
        checksum: str,
        default_script: str | None,
        data: bytes,
        package: ValidatedSkillPackage,
        actor: AuditActor,
    ) -> SkillArtifact:
        return SkillArtifact(
            id=artifact_id,
            skill_id=skill_id,
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
            default_script=default_script,
            package_size=len(data),
            validation_status=VALIDATION_READY,
            created_by=actor.account_id,
        )

    def _validated_default_script(
        self,
        value: str | None,
        package: ValidatedSkillPackage,
    ) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".py":
            raise AppError(ErrorCode.SKILL_PACKAGE_INVALID)
        if not any(
            entry["path"] == value or entry["path"].endswith(f"/{value}")
            for entry in package.files
        ):
            raise AppError(ErrorCode.SKILL_PACKAGE_INVALID)
        return value

    async def _require_absent_version(
        self,
        skill_id: uuid.UUID,
        version: str,
        checksum: str,
    ) -> None:
        if await self._skills.find_artifact_by_version(skill_id, version) is not None:
            raise AppError(ErrorCode.SKILL_VERSION_EXISTS)
        if await self._skills.find_artifact_by_checksum(skill_id, checksum) is not None:
            raise AppError(ErrorCode.SKILL_VERSION_EXISTS)

    async def _require_platform_user(self, tenant_id: str, user_id: uuid.UUID) -> PlatformUser:
        user = await self._platform_users.get(tenant_id, user_id)
        if user is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return user

    async def _require_agent(self, tenant_id: str, agent_id: uuid.UUID) -> AgentDefinition:
        agent = await self._agents.get(tenant_id, agent_id)
        if agent is None:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        return agent

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        resource_type: str,
        resource_id: uuid.UUID,
        action: str,
        before: dict[str, Any] | None,
        skill: Skill,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            before=before,
            after=skill_snapshot(skill),
        )

    async def _record_artifact_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        artifact: SkillArtifact,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_ARTIFACT,
            resource_id=artifact.id,
            action=action,
            before=before,
            after=artifact_snapshot(artifact),
        )

    async def _record_grant_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        grant: SkillUserGrant,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_GRANT,
            resource_id=grant.id,
            action="DELETE" if grant.is_deleted else "CREATE",
            before=before,
            after=grant_snapshot(grant),
        )

    async def _record_binding_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        binding: AgentSkillBinding,
        before: dict[str, Any] | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_BINDING,
            resource_id=binding.id,
            action="DELETE" if binding.is_deleted else "CREATE",
            before=before,
            after=binding_snapshot(binding),
        )
