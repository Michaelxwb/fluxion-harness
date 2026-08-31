"""ContextResolver：Identity→Snapshot 十段解析管线（closure TASK-007）。

阶段：identity → user → agent → runtime → profile → memory → capability →
credential → policy → snapshot。每段记录 resolved version + 耗时进
resolution_trace（关联 trace_id）。全部 fail-closed：解析失败抛
ContextResolutionError（slug + status），不产出缺字段 digest。性能：
L1 内存缓存必备（Redis L2 可选增强，正确性不依赖）。

依赖方向（规则 7 / services 禁 ORM query）：本服务只依赖 RegistryStore/
ChannelRegistryStore Contract 与注入的 MemoryRetriever，不持有 SQLAlchemy
engine、不写 raw select（TASK-002 收口）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from fluxion.agents.definitions import AgentDefinition
from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.registry import ChannelRegistryStore
from fluxion.resources import ResourceKind, RuntimeProfile, SkillDefinition
from fluxion.resources.contracts import (
    EffectiveCapability,
    ExactResourceVersion,
    ExecutionSnapshot,
    MemoryEntryRef,
    MemoryManifest,
    ModelPolicy,
)
from fluxion.resources.snapshot_digest import canonical_digest
from fluxion.runtime.capabilities import EffectiveCapabilityResolver

logger = logging.getLogger(__name__)


class _SkillSpecView(SkillDefinition):
    """resolver 侧 skill spec 读取视图（extra=ignore 容忍存量 spec 扩展字段）。"""

    model_config = {"extra": "ignore"}


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
        store: ChannelRegistryStore,
        *,
        memory_budget: int = 5,
        credential_resolver: Any | None = None,
        memory_retriever: PersonalMemoryRetriever | None = None,
    ) -> None:
        self._store = store
        self._memory_budget = memory_budget
        # closure TASK-007（E-02）：binding 带凭据引用时经真实 CredentialResolver
        # 解析；解析失败 → fail-closed（credential_not_resolvable）。
        self._credential_resolver = credential_resolver
        # L1 缓存（remediation §13.5 / design §3.5）：key = (tenant, agent, user)
        self._l1_cache: dict[str, tuple[ResolveResult, float, int]] = {}
        # TTL=0 禁用跨执行缓存：主 invoke 使用 latest-published 解析，缓存会违反
        # REQ-EXE-003（热发布后新执行取 latest）。性能优化待 registry revision 正确
        # bump 的失效机制就绪后再启用。
        self._l1_cache_ttl: float = 0.0  # 秒
        # Memory 段经注入的 PersonalMemoryRetriever（P-04 / §13.4）。未注入时
        # 降级空 manifest（不阻塞、不持有 engine）。
        self._memory_retriever = memory_retriever

    async def resolve(
        self,
        selector: ResolverSelector,
        *,
        session_id: str,
        memory_query: str | None = None,
        memory_budget: int | None = None,
    ) -> ResolveResult:
        del session_id  # session 维度由调用方承载；本管线按 (tenant, agent, user) 解析
        trace: list[StageTrace] = []

        def _stage(stage: str, version: str | None, started: float) -> None:
            trace.append(StageTrace(stage, version, (time.perf_counter() - started) * 1000))

        # L1 缓存检查（remediation §13.5：同 key 短路，不重复查库；按 registry revision
        # 失效，publish 即刷新——避免热发布后 30s 内仍返回旧版本，守住 REQ-EXE-003）。
        cache_key = f"{selector.tenant_id}:{selector.agent_id}:{selector.user_id}"
        revision = await self._store.read_revision(tenant_id=selector.tenant_id)
        cached = self._l1_cache.get(cache_key)
        if cached is not None:
            result, ts, cached_revision = cached
            if cached_revision == revision and time.monotonic() - ts < self._l1_cache_ttl:
                return replace(
                    result,
                    snapshot=result.snapshot.model_copy(
                        update={
                            "execution_id": f"exec_{uuid4_hex()}",
                            "trace_id": f"trace_{uuid4_hex()}",
                        }
                    ),
                )
            del self._l1_cache[cache_key]
        started = time.perf_counter()
        # 1. identity：user_id 视为 platform_user_id（Channel 层已解析；无前缀
        # 直传，channel_user_id 回退见 _resolve_platform_user）
        started = time.perf_counter()
        if not selector.tenant_id.strip() or not selector.user_id.strip():
            raise ContextResolutionError(code="identity_missing", message="identity required", status_code=401)
        platform_user_id = await self._resolve_platform_user(selector.tenant_id, selector.user_id)
        _stage("identity", platform_user_id, started)

        # 2. user：User Profile 版本（可选；pin 校验 fail-closed）
        started = time.perf_counter()
        user_profile_version = selector.user_profile_version
        if user_profile_version is not None:
            row = await self._store.get_user_profile_at(
                tenant_id=selector.tenant_id,
                platform_user_id=selector.user_id,
                version=user_profile_version,
            )
            if row is None:
                raise ContextResolutionError(code="user_profile_not_found", message=f"user profile @{user_profile_version} not found", status_code=404)
        else:
            user_profile_version = await self._latest_user_profile_version(selector.tenant_id, selector.user_id)
        _stage("user", user_profile_version, started)

        # 3. agent：AgentDefinition（latest published，或 selector pin）
        started = time.perf_counter()
        agent = await self._store.get(
            ResourceKind.AGENT_DEFINITION, selector.agent_id, tenant_id=selector.tenant_id
        )
        if agent is None:
            raise ContextResolutionError(code="agent_not_found", message=f"agent_not_found: {selector.agent_id}", status_code=404)
        _stage("agent", agent.version, started)

        # 4. runtime：Agent.runtime_profile_ref → RuntimeProfile；缺省同名回退
        started = time.perf_counter()
        agent_spec = AgentDefinition.model_validate(agent.spec_json)
        profile_id = (
            agent_spec.runtime_profile_ref.id if agent_spec.runtime_profile_ref else selector.agent_id
        )
        profile_version = selector.runtime_profile_version or agent_spec.runtime_profile_ref.version if agent_spec.runtime_profile_ref else "latest-published"
        profile_row = await self._store.get(
            ResourceKind.RUNTIME_PROFILE, profile_id, tenant_id=selector.tenant_id,
            version=None if profile_version == "latest-published" else profile_version,
        )
        if profile_row is None:
            raise ContextResolutionError(code="runtime_profile_not_found", message=f"{profile_id}@{profile_version} not found", status_code=404)
        _stage("runtime", profile_row.version, started)

        # 5. model：ModelPolicy（provider 来自 AgentDefinition.model_ref；failover 取
        # RuntimeProfile.model_failover typed 字段，TASK-011 删除 legacy executor_config 回退）
        profile_spec = RuntimeProfile.model_validate(profile_row.spec_json)
        failover = list(profile_spec.model_failover)
        model_resolution = ModelPolicy(
            provider_ref=ExactResourceVersion(
                id=agent_spec.model_ref.id, version=agent_spec.model_ref.version
            ),
            failover=[ExactResourceVersion(id=f, version="latest-published") for f in failover],
            timeout_ms=profile_spec.request_timeout_ms,
            max_rounds=profile_spec.max_rounds,
            deadline_ms=max(profile_spec.request_timeout_ms * 2, 120_000),
        )

        # 6. profile：User Profile 版本已解析（stage 2）

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
        (
            skill_versions,
            mcp_versions,
            _plugin_versions,
            skill_instructions,
            skill_required_capabilities,
        ) = await self._resolve_capability_versions(
            selector.tenant_id, agent_spec.capabilities, selector.user_id
        )
        _stage("capability", None, started)

        # 8. credential：bindings credential_ref → versions（只存 ref→version）
        started = time.perf_counter()
        credential_versions = await self._credential_versions(selector.tenant_id, platform_user_id or selector.user_id)
        _stage("credential", None, started)

        # 9. policy：tenant policy version（经 tenant POLICY binding 解析；无则 latest-published）
        started = time.perf_counter()
        policy_bindings = await self._store.list_bindings(
            subject_type="tenant",
            subject_id=selector.tenant_id,
            tenant_id=selector.tenant_id,
            resource_type=ResourceKind.POLICY,
        )
        policy_versions = {
            binding.resource_id: binding.resource_version_selector
            for binding in policy_bindings
        }
        _stage("policy", policy_versions.get("tenant"), started)

        # effective permissions（tool 授权三元组，构建期冻结，执行期不再实时重算）
        agent_tool_refs = {c["capability_ref"] for c in capabilities if c["type"] == "tool"}
        # TASK-006：closure 校验——skill 的 required_capabilities 必须已被 agent 声明
        # 覆盖；skill 不再隐式扩张 agent 工具权限（RULE-04），越出则 fail-closed。
        undeclared = set(skill_required_capabilities) - agent_tool_refs
        if undeclared:
            raise ContextResolutionError(
                code="skill_closure_violation",
                message=f"skill requires capabilities not declared by agent: {sorted(undeclared)}",
                status_code=422,
            )
        agent_tools = agent_tool_refs
        grants = await self._store.list_capability_grants(
            tenant_id=selector.tenant_id,
            platform_user_id=platform_user_id or selector.user_id,
        )
        user_tools = {g.capability_ref for g in grants if g.capability_kind == "tool"}
        policy_allowed, policy_denied, policy_configured = (
            await EffectiveCapabilityResolver(self._store).tenant_policy_tools(
                tenant_id=selector.tenant_id
            )
        )
        if not policy_configured:
            tenant_tools = set(user_tools)
        elif policy_allowed:
            tenant_tools = set(policy_allowed)
        else:
            tenant_tools = set(user_tools)
        if policy_denied:
            user_tools = user_tools - policy_denied
            agent_tools = agent_tools - policy_denied
            tenant_tools = tenant_tools - policy_denied

        # 10. snapshot：V2 全字段 + canonical digest
        started = time.perf_counter()
        snapshot = ExecutionSnapshot(
            execution_id=f"exec_{uuid4_hex()}",
            tenant_id=selector.tenant_id,
            user_id=platform_user_id or selector.user_id,
            runtime_profile_id=profile_row.id,
            runtime_profile_version=profile_row.version,
            agent_definition_id=agent.id,
            agent_definition_version=agent.version,
            model_resolution=model_resolution,
            effective_capability=EffectiveCapability(
                skills=skill_versions,
                mcps=mcp_versions,
                workflows=[agent_spec.workflow_ref.id] if agent_spec.workflow_ref else [],
                tools=[c["capability_ref"] for c in capabilities if c["type"] == "tool"],
            ),
            effective_permissions={
                "agent_tools": sorted(agent_tools),
                "user_tools": sorted(user_tools),
                "tenant_tools": sorted(tenant_tools),
            },
            trace_id=f"trace_{uuid4_hex()}",
            system_prompt=agent_spec.system_prompt,
            skill_instructions=skill_instructions,
            skill_required_capabilities=skill_required_capabilities,
            skill_versions=skill_versions,
            mcp_versions=mcp_versions,
            plugin_versions={agent_spec.model_ref.id: agent_spec.model_ref.version},
            policy_version=policy_versions.get("tenant"),
            binding_versions={
                b.binding_id: b.resource_version_selector
                for b in await self._store.list_bindings(
                    subject_type="user",
                    subject_id=selector.user_id,
                    tenant_id=selector.tenant_id,
                )
            },
            user_profile_version=user_profile_version,
            policy_versions=policy_versions,
            credential_versions=credential_versions,
            memory_manifest=manifest,
        )
        snapshot = snapshot.model_copy(update={"snapshot_digest": canonical_digest(snapshot)})
        _stage("snapshot", (snapshot.snapshot_digest or "")[:12], started)

        user_context = {
            "user_id": platform_user_id or selector.user_id,
            "profile_version": user_profile_version,
            "capabilities": capabilities,
            "memory_manifest": manifest.model_dump(),
        }
        budget_used = len(manifest.entry_refs)
        result = ResolveResult(
            snapshot=snapshot,
            user_context=user_context,
            resolution_trace=trace,
            budget_used=budget_used,
        )
        self._l1_cache[cache_key] = (result, time.monotonic(), revision)
        return result

    async def _latest_user_profile_version(self, tenant_id: str, user_id: str) -> str | None:
        row = await self._store.get_latest_user_profile(
            tenant_id=tenant_id, platform_user_id=user_id
        )
        return str(row["version"]) if row else None

    async def _memory_manifest(
        self,
        tenant_id: str,
        user_id: str,
        memory_query: str | None,
        memory_budget: int | None,
    ) -> MemoryManifest:
        """Memory 段：经注入的 PersonalMemoryRetriever recall（SemanticStoreProvider.recall）。"""
        budget = memory_budget if memory_budget is not None else self._memory_budget
        if self._memory_retriever is None:
            return MemoryManifest(entry_refs=[], content_hash="unavailable", truncated=True)
        try:
            entries = await self._memory_retriever.recall(
                tenant_id, user_id, query=memory_query or "", top_k=budget + 10
            )
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
        """Identity 段：user_id 无前缀时视为 platform_user_id 直传（Channel/API 已解析）。"""
        if user_id.startswith(("migration:", "user-")):
            return user_id
        resolved = await self._store.resolve_platform_user_by_channel_id(
            tenant_id=tenant_id, channel_user_id=user_id
        )
        return resolved or user_id

    async def _resolve_capability_versions(
        self, tenant_id: str, capabilities: list[Any], user_id: str
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        dict[str, str],
        dict[str, str],
        list[str],
    ]:
        """按 Agent capabilities + user binding 解析 skill/mcp 实际 published 版本。

        skill 语义 = Agent baseline（agent 声明的 capability）∪ user binding grant
        （管理员经 grant/binding 给用户扩 skill，binding 可覆盖版本）。tool 类型不解析
        版本：builtin/runtime tool 不是版本化 Resource。skill 额外提取 instructions
        （注入 system prompt）与 required_capabilities（closure 校验所需能力）。
        """
        # Agent baseline：agent 声明的 skill pin（capability_ref → version_pin）
        agent_skill_pins: dict[str, str] = {}
        for cap in capabilities:
            if cap.type == "skill":
                agent_skill_pins[cap.capability_ref] = cap.version_pin

        # user binding grant：管理员给用户扩的 skill（ref → version selector）
        bindings = await self._store.list_bindings(
            subject_type="user",
            subject_id=user_id,
            tenant_id=tenant_id,
            resource_type=ResourceKind.SKILL,
        )
        binding_granted = {binding.resource_id for binding in bindings}
        effective_skill_pins = dict(agent_skill_pins)
        for binding in bindings:
            if binding.resource_id in effective_skill_pins:
                # binding 覆盖版本（仅当 agent baseline 未显式 pin 时）
                if effective_skill_pins[binding.resource_id] == "latest-published":
                    effective_skill_pins[binding.resource_id] = binding.resource_version_selector
            else:
                effective_skill_pins[binding.resource_id] = binding.resource_version_selector

        skill_versions: dict[str, str] = {}
        mcp_versions: dict[str, str] = {}
        plugin_versions: dict[str, str] = {}
        skill_instructions: dict[str, str] = {}
        required_capabilities: set[str] = set()
        for ref, version_pin in effective_skill_pins.items():
            row = await self._store.get(
                ResourceKind.SKILL,
                ref,
                tenant_id=tenant_id,
                version=None if version_pin == "latest-published" else version_pin,
            )
            if row is None:
                if ref in agent_skill_pins:
                    # agent 声明的 skill 缺 pinned 版本 → fail-closed（不静默降级）
                    raise ContextResolutionError(
                        code="skill_version_not_found",
                        message=f"skill {ref}@{version_pin} not found",
                        status_code=404,
                    )
                continue
            parsed = _SkillSpecView.model_validate(row.spec_json)
            # TASK-004：private skill 仅 grant 用户可用（spec 级 visibility）
            if parsed.visibility == "private" and ref not in binding_granted:
                continue
            skill_versions[ref] = row.version
            if parsed.instructions.strip():
                skill_instructions[ref] = parsed.instructions.strip()
            required_capabilities.update(item for item in parsed.required_capabilities if item.strip())

        # mcp 版本（仅 agent 声明；mcp server 级 grant 语义见 TASK-004 后续）
        for cap in capabilities:
            if cap.type != "mcp":
                continue
            row = await self._store.get(
                ResourceKind.MCP,
                cap.capability_ref,
                tenant_id=tenant_id,
                version=None if cap.version_pin == "latest-published" else cap.version_pin,
            )
            if row is not None:
                mcp_versions[cap.capability_ref] = row.version

        return (
            skill_versions,
            mcp_versions,
            plugin_versions,
            skill_instructions,
            sorted(required_capabilities),
        )

    async def _credential_versions(self, tenant_id: str, user_id: str) -> dict[str, str]:
        bindings = await self._store.list_bindings(
            subject_type="user", subject_id=user_id, tenant_id=tenant_id
        )
        versions: dict[str, str] = {}
        for binding in bindings:
            ref = binding.credential_ref
            if not ref:
                continue
            if self._credential_resolver is None:
                versions[ref] = "1"  # 未配置 resolver：仅记录引用（dev）
                continue
            try:
                resolved = await self._credential_resolver.resolve_with_metadata(
                    ref, tenant_id=tenant_id
                )
            except Exception as exc:
                raise ContextResolutionError(
                    code="credential_not_resolvable",
                    message=f"credential {ref} not resolvable",
                    status_code=422,
                ) from exc
            versions[ref] = resolved.version
        return versions


class ContextResolverSnapshotBuilder:
    """把 ContextResolver.resolve 适配到 AgentRuntime 的 build(request) 接口。

    TASK-002：主 invoke 从 ExecutionSnapshotBuilder 切到 ContextResolver。
    request 只需暴露 tenant_id/user_id/agent_definition_id/runtime_profile_id/
    runtime_profile_version_selector/session_id（与 RequestContext 兼容）。
    """

    def __init__(self, resolver: ContextResolver) -> None:
        self._resolver = resolver

    async def build(self, request: Any) -> ExecutionSnapshot:
        agent_id = request.agent_definition_id or request.runtime_profile_id
        selector = ResolverSelector(
            tenant_id=request.tenant_id,
            agent_id=agent_id,
            user_id=request.user_id,
            runtime_profile_version=(
                None
                if request.runtime_profile_version_selector == "latest-published"
                else request.runtime_profile_version_selector
            ),
        )
        result = await self._resolver.resolve(selector, session_id=request.session_id)
        snapshot = result.snapshot
        # 请求 trace_id 贯通到 snapshot（端到端关联：request → execution → trace store）。
        # trace_id 属运行时字段，不进 canonical digest（snapshot_digest._RUNTIME_FIELDS），
        # 覆盖不影响跨实例等价性。
        if getattr(request, "trace_id", None) and snapshot.trace_id != request.trace_id:
            snapshot = snapshot.model_copy(update={"trace_id": request.trace_id})
        return snapshot


def uuid4_hex() -> str:
    import uuid

    return uuid.uuid4().hex


def _short_hash(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
