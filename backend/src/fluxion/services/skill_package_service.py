"""TASK-009：Skill Package 发布服务。

链路：ZIP 上传 → parse/validate → artifact 入 ObjectStore →
capability_skills 行 → SKILL ResourceDefinition 物化（instructions 来源）→ publish。
运行时复用既有 ResourceDefinition 解析路径（零行为分叉）。
"""

from __future__ import annotations

from dataclasses import dataclass

from fluxion.plugins.artifact.refs import build_artifact_ref
from fluxion.plugins.contracts import ArtifactStoreProvider
from fluxion.registry.store import CapabilitySkillRecord, RegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.services.skill_package import (
    build_skill_package,
    parse_skill_package,
)

SKILL_ARTIFACT_NAMESPACE = "skills"


@dataclass(frozen=True, slots=True)
class SkillPackagePublication:
    skill_id: str
    version: str
    artifact_uri: str
    artifact_hash: str


class SkillPackageService:
    """Skill Package 发布（Console :publish 与测试共用）。"""

    def __init__(
        self,
        store: RegistryStore,
        artifacts: ArtifactStoreProvider,
    ) -> None:
        self._store = store
        self._artifacts = artifacts

    async def publish_package(
        self, *, tenant_id: str, data: bytes, skill_id: str | None = None
    ) -> SkillPackagePublication:
        """解析 → 落 artifact → 写行 → 物化并发布 SKILL 定义。"""
        bundle = parse_skill_package(data)
        resolved_id = skill_id or bundle.manifest.name
        version = bundle.manifest.version
        key = f"{resolved_id}-{version}.zip"
        await self._artifacts.put(tenant_id, SKILL_ARTIFACT_NAMESPACE, key, data)
        artifact_uri = build_artifact_ref(tenant_id, SKILL_ARTIFACT_NAMESPACE, key, version)
        spec = build_skill_package(bundle)
        # capability_skills 行单次写入 published（无真数据迁移期不保留 draft 行；
        # UNIQUE(tenant, skill, version) 下重复发布同版本即冲突，符合不可变语义）。
        await self._store.put(
            ResourceDefinition(
                tenant_id=tenant_id,
                kind=ResourceKind.SKILL,
                id=resolved_id,
                version=version,
                status=ResourceStatus.DRAFT,
                spec_json={
                    "name": bundle.manifest.name,
                    "instructions": spec["instructions"],
                    "required_capabilities": spec["required_capabilities"],
                    "visibility": "public",
                },
            )
        )
        published = await self._store.publish(
            ResourceKind.SKILL, resolved_id, tenant_id=tenant_id, version=version
        )
        # capability_skills 行状态同步为 published（行写入器在 store 侧无状态机，
        # 与 ResourceDefinition 发布原子性由调用方约定；失败即整体失败）。
        await self._store.put_capability_skill(
            CapabilitySkillRecord(
                skill_id=resolved_id,
                tenant_id=tenant_id,
                name=bundle.manifest.name,
                description=bundle.manifest.description,
                version=int(version) if version.isdigit() else 1,
                status=ResourceStatus.PUBLISHED.value,
                artifact_uri=artifact_uri,
                artifact_hash=bundle.artifact_hash,
                manifest_json={
                    "name": bundle.manifest.name,
                    "version": bundle.manifest.version,
                    "description": bundle.manifest.description,
                    "required_capabilities": list(bundle.manifest.required_capabilities),
                    "scripts": list(bundle.manifest.scripts),
                },
                knowledge_manifest_json={"files": list(bundle.knowledge_files)},
            )
        )
        return SkillPackagePublication(
            skill_id=resolved_id,
            version=published.version,
            artifact_uri=artifact_uri,
            artifact_hash=bundle.artifact_hash,
        )
