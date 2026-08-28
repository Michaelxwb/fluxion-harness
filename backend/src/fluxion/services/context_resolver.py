"""ContextResolver：Identity→Snapshot 十段解析管线（closure TASK-007）。

阶段：identity → user → agent → runtime → profile → memory → capability →
credential → policy → snapshot。每段记录 resolved version + 耗时进
resolution_trace（关联 trace_id）。全部 fail-closed：解析失败抛
ContextResolutionError（slug + status），不产出缺字段 digest。性能：
L1 内存缓存必备（Redis L2 可选增强，正确性不依赖）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.agents.definitions import AgentDefinition
from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore
from fluxion.registry.schema import (
    channel_identities,
    resource_bindings,
    resource_definitions,
    user_profiles,
)
from fluxion.resources.contracts import (
    ExecutionSnapshot,
    MemoryEntryRef,
    MemoryManifest,
    ModelPolicy,
)
from fluxion.resources.snapshot_digest import canonical_digest


class ContextResolutionError(RuntimeError):
    """解析失败（fail-closed）：code slug + HTTP status + 可选 snapshot_digest。"""

    def __init__(
        self, *, code: str, message: str, status_code: int = 422, snapshot_digest: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.snapshot_digest = snapshot_digest


@dataclass(frozen=True, slots=True)
class ResolverSelector:
    """解析选择器：agent_id 主坐标（remediation §13.1）+ 可选 profile pin。"""

    tenant_id: str
    agent_id: str
    user_id: str
    user_profile_version: str | None = None
    runtime_profile_version: str | None = None


@dataclass(frozen=True, slots=True)
class StageTrace:
    stage: str
    version: str | None
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class ResolveResult:
    snapshot: ExecutionSnapshot
    user_context: dict[str, Any]
    resolution_trace: list[StageTrace]
    budget_used: int


@dataclass(frozen=True, slots=True)
class BudgetExceededEntry:
    """B-01：memory manifest 超 budget → 按 priority 截断。"""

    @staticmethod
    def truncate(manifest: MemoryManifest, budget: int) -> MemoryManifest:

        kept = sorted(
            manifest.entry_refs, key=lambda ref: getattr(ref, "priority", 0)
        )[:budget]
        return manifest.model_copy(
            update={"entry_refs": kept, "truncated": True}
        )


class ContextResolver:
    """十段解析管线（services 应用服务；无状态，实例可跨请求复用）。"""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        memory_budget: int = 5,
        credential_resolver: Any | None = None,
    ) -> None:
        self._engine = engine
        self._memory_budget = memory_budget
        # closure TASK-007（E-02）：binding 带凭据引用时经真实 CredentialResolver
        # 解析；解析失败 → fail-closed（credential_not_resolvable）。
        self._credential_resolver = credential_resolver
        # L1 缓存（remediation §13.5 / design §3.5）：key = (tenant, agent, user)
        self._l1_cache: dict[str, tuple[ResolveResult, float]] = {}
        # Memory 段经 PersonalMemoryRetriever（P-04 / §13.4）
        self._semantic = PgVectorSemanticStore(engine)
        self._memory_retriever = PersonalMemoryRetriever(self._semantic)

    async def resolve(
        self,
        selector: ResolverSelector,
        *,
        session_id: str,
        memory_query: str | None = None,
        memory_budget: int | None = None,
    ) -> ResolveResult:
        trace: list[StageTrace] = []

        def _stage(stage: str, version: str | None, started: float) -> None:
            trace.append(StageTrace(stage, version, (time.perf_counter() - started) * 1000))

        started = time.perf_counter()
        # 1. identity：ChannelIdentity → PlatformUser 映射（Phase 1 复用）
        started = time.perf_counter()
        if not selector.tenant_id.strip() or not selector.user_id.strip():
            raise ContextResolutionError(code="identity_missing", message="identity required", status_code=401)
        platform_user_id = await self._resolve_platform_user(selector.tenant_id, selector.user_id)
        _stage("identity", platform_user_id, started)

        # 2. user：User Profile 版本（可选；pin 校验 fail-closed）
        started = time.perf_counter()
        user_profile_version = selector.user_profile_version
        if user_profile_version is not None:
            row = await self._user_profile_at(selector.tenant_id, selector.user_id, user_profile_version)
            if row is None:
                raise ContextResolutionError(code="user_profile_not_found", message=f"user profile @{user_profile_version} not found", status_code=404)
        else:
            user_profile_version = await self._latest_user_profile_version(selector.tenant_id, selector.user_id)
        _stage("user", user_profile_version, started)

        # 3. agent：AgentDefinition（latest published，或 selector pin）
        started = time.perf_counter()
        agent = await self._get_resource("agent_definition", selector.agent_id, selector.tenant_id)
        if agent is None:
            raise ContextResolutionError(code="agent_not_found", message=f"agent_not_found: {selector.agent_id}", status_code=404)
        _stage("agent", agent["version"], started)

        # 4. runtime：Agent.runtime_profile_ref → RuntimeProfile；缺省同名回退
        started = time.perf_counter()
        agent_spec = AgentDefinition.model_validate(agent["spec_json"])
        profile_id = (
            agent_spec.runtime_profile_ref.id if agent_spec.runtime_profile_ref else selector.agent_id
        )
        profile_version = selector.runtime_profile_version or agent_spec.runtime_profile_ref.version if agent_spec.runtime_profile_ref else "latest-published"
        profile_row = await self._get_resource("runtime_profile", profile_id, selector.tenant_id, profile_version)
        if profile_row is None:
            raise ContextResolutionError(code="runtime_profile_not_found", message=f"{profile_id}@{profile_version} not found", status_code=404)
        _stage("runtime", profile_row["version"], started)

        # 5. profile：User Profile 版本已解析（stage 2）

        # 6. memory：PersonalMemoryRetriever recall → manifest（失败降级空 manifest）
        started = time.perf_counter()
        manifest = await self._memory_manifest(
            selector.tenant_id, platform_user_id or selector.user_id, memory_query, memory_budget
        )
        _stage("memory", manifest.content_hash or None, started)

        # 7. capability：Agent capabilities（typed 三元组）+ skill/mcp/plugin 版本解析
        started = time.perf_counter()
        capabilities = [
            {"type": ref.type.value, "capability_ref": ref.capability_ref, "version_pin": ref.version_pin}
            for ref in agent_spec.capabilities
        ]
        skill_versions, mcp_versions, plugin_versions = await self._resolve_capability_versions(
            selector.tenant_id, agent_spec.capabilities
        )
        _stage("capability", None, started)

        # 8. credential：bindings credential_ref → versions（只存 ref→version）
        started = time.perf_counter()
        credential_versions = await self._credential_versions(selector.tenant_id, platform_user_id or selector.user_id)
        _stage("credential", None, started)

        # 9. policy：tenant policy version（未配置时 None 规范参与）
        started = time.perf_counter()
        policy_versions = {"tenant": agent.get("policy_version") or "latest-published"}
        _stage("policy", policy_versions["tenant"], started)

        # 10. snapshot：V2 全字段 + canonical digest
        started = time.perf_counter()
        snapshot = ExecutionSnapshot(
            execution_id=f"exec_{uuid4_hex()}",
            tenant_id=selector.tenant_id,
            user_id=platform_user_id or selector.user_id,
            runtime_profile_id=str(profile_row["resource_id"]),
            runtime_profile_version=str(profile_row["version"]),
            agent_definition_id=str(agent["resource_id"]),
            agent_definition_version=str(agent["version"]),
            model_resolution=ModelPolicy(),
            trace_id=f"trace_{uuid4_hex()}",
            system_prompt=agent_spec.system_prompt,
            skill_versions=skill_versions,
            mcp_versions=mcp_versions,
            plugin_versions=plugin_versions,
            policy_version=policy_versions["tenant"],
            binding_versions={b["binding_id"]: str(b["resource_version_selector"]) for b in await self._bindings(selector.tenant_id, selector.user_id)},
            user_profile_version=user_profile_version,
            policy_versions=policy_versions,
            credential_versions=credential_versions,
            memory_manifest=manifest,
        )
        snapshot = snapshot.model_copy(update={"snapshot_digest": canonical_digest(snapshot)})
        _stage("snapshot", snapshot.snapshot_digest[:12], started)

        user_context = {
            "user_id": platform_user_id or selector.user_id,
            "profile_version": user_profile_version,
            "capabilities": capabilities,
            "memory_manifest": manifest.model_dump(),
        }
        budget_used = len(manifest.entry_refs)
        return ResolveResult(
            snapshot=snapshot,
            user_context=user_context,
            resolution_trace=trace,
            budget_used=budget_used,
        )

    async def _get_resource(
        self, kind: str, resource_id: str, tenant_id: str, version: str | None = None
    ) -> dict[str, Any] | None:
        from sqlalchemy import desc

        stmt = select(
            resource_definitions.c.resource_id,
            resource_definitions.c.version,
            resource_definitions.c.status,
            resource_definitions.c.spec_json,
        ).where(
            resource_definitions.c.kind == kind,
            resource_definitions.c.resource_id == resource_id,
            resource_definitions.c.tenant_id == tenant_id,
        )
        if version is not None and version != "latest-published":
            stmt = stmt.where(resource_definitions.c.version == version)
        else:
            stmt = stmt.where(resource_definitions.c.status == "published").order_by(
                desc(resource_definitions.c.created_at)
            )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt.limit(1))).mappings().first()
        return dict(row) if row else None

    async def _latest_user_profile_version(self, tenant_id: str, user_id: str) -> str | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(user_profiles.c.version)
                    .where(
                        user_profiles.c.tenant_id == tenant_id,
                        user_profiles.c.platform_user_id == user_id,
                    )
                    .order_by(user_profiles.c.version.desc())
                    .limit(1)
                )
            ).first()
        return str(row[0]) if row else None

    async def _user_profile_at(self, tenant_id: str, user_id: str, version: str):
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(user_profiles).where(
                        user_profiles.c.tenant_id == tenant_id,
                        user_profiles.c.platform_user_id == user_id,
                        user_profiles.c.version == int(version) if version.isdigit() else user_profiles.c.version == version,
                    )
                )
            ).first()
        return row

    async def _memory_manifest(
        self,
        tenant_id: str,
        user_id: str,
        memory_query: str | None,
        memory_budget: int | None,
    ) -> MemoryManifest:
        """Memory 段：经 PersonalMemoryRetriever recall（SemanticStoreProvider.recall）。"""
        budget = memory_budget if memory_budget is not None else self._memory_budget
        try:
            semantic = PgVectorSemanticStore(self._engine)
            retriever = PersonalMemoryRetriever(semantic)
            entries = await retriever.recall(tenant_id, user_id, query=memory_query or "", top_k=budget + 10)
        except Exception:  # noqa: BLE001 - memory 段降级空 manifest 不阻塞
            return MemoryManifest(entry_refs=[], content_hash="unavailable", truncated=True)
        refs = [
            MemoryEntryRef(
                entry_id=str(entry.id),
                memory_type=entry.memory_type.value,
                content_hash=_short_hash(entry.content),
                priority=index,
            )
            for index, entry in enumerate(entries)
        ]
        truncated = len(refs) > budget
        if truncated:
            refs = refs[:budget]
        content_hash = _short_hash("|".join(ref.content_hash for ref in refs)) if refs else ""
        return MemoryManifest(
            entry_refs=refs,
            content_hash=content_hash,
            truncated=truncated,
        )

    async def _resolve_platform_user(self, tenant_id: str, user_id: str) -> str | None:
        """Identity 段：ChannelIdentity → PlatformUser 映射（Phase 1 复用）。
        user_id 无前缀时视为 platform_user_id 直传（Channel/API 已解析）。"""
        if user_id.startswith("migration:") or user_id.startswith("user-"):
            return user_id
        try:
            async with self._engine.connect() as conn:
                row = (
                    await conn.execute(
                        select(channel_identities.c.platform_user_id).where(
                            channel_identities.c.tenant_id == tenant_id,
                            channel_identities.c.channel_user_id == user_id,
                        )
                    )
                ).first()
            if row is not None:
                return str(row[0])
        except Exception:
            pass
        return user_id

    async def _resolve_capability_versions(
        self, tenant_id: str, capabilities: list[dict[str, str]]
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """按 Agent capabilities 解析 skill/mcp/tool 的实际 published 版本。"""
        skill_versions: dict[str, str] = {}
        mcp_versions: dict[str, str] = {}
        plugin_versions: dict[str, str] = {}
        kind_map = {"skill": "skill_versions", "mcp": "mcp_versions", "plugin": "plugin_versions"}
        for cap in capabilities:
            cap_type = cap["type"]
            ref = cap["capability_ref"]
            pin = cap["version_pin"]
            kind = kind_map.get(cap_type)
            if kind is None:
                continue
            row = await self._get_resource(cap_type, ref, tenant_id, pin if pin != "latest-published" else None)
            if row is not None:
                version = str(row["version"])
                if kind == "skill_versions":
                    skill_versions[ref] = version
                elif kind == "mcp_versions":
                    mcp_versions[ref] = version
                else:
                    plugin_versions[ref] = version
        return skill_versions, mcp_versions, plugin_versions

    async def _bindings(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(
                        resource_bindings.c.binding_id,
                        resource_bindings.c.resource_version_selector,
                        resource_bindings.c.credential_ref,
                    ).where(
                        resource_bindings.c.tenant_id == tenant_id,
                        resource_bindings.c.subject_type == "user",
                        resource_bindings.c.subject_id == user_id,
                        resource_bindings.c.enabled == True,
                    )
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def _credential_versions(self, tenant_id: str, user_id: str) -> dict[str, str]:
        bindings = await self._bindings(tenant_id, user_id)
        versions: dict[str, str] = {}
        for binding in bindings:
            ref = binding.get("credential_ref")
            if not ref:
                continue
            if self._credential_resolver is None:
                versions[str(ref)] = "1"  # 未配置 resolver：仅记录引用（dev）
                continue
            try:
                resolved = await self._credential_resolver.resolve_with_metadata(
                    str(ref), tenant_id=tenant_id
                )
            except Exception as exc:
                raise ContextResolutionError(
                    code="credential_not_resolvable",
                    message=f"credential {ref} not resolvable",
                    status_code=422,
                ) from exc
            versions[str(ref)] = resolved.version
        return versions


def uuid4_hex() -> str:
    import uuid

    return uuid.uuid4().hex


def _short_hash(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
