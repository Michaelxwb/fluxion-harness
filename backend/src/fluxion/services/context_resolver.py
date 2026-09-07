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

import time
from dataclasses import dataclass, replace
from typing import Any

from fluxion.agents.definitions import AgentDefinition
from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.registry import ChannelRegistryStore
from fluxion.resources import (
    ResolvedModelRoute,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
)
from fluxion.resources.contracts import (
    EffectiveCapability,
    ExecutionSnapshot,
    MemoryManifest,
    ModelPolicy,
)
from fluxion.resources.snapshot_digest import canonical_digest
from fluxion.runtime.capabilities import EffectiveCapabilityResolver
from fluxion.runtime.model_providers import resolve_effective_credential_ref
from fluxion.services.context_resolution_support import (
    ContextResolutionError,
    ContextResolutionSupport,
)
from fluxion.services.runtime_contracts import validate_run_identity
from fluxion.services.runtime_profile_resolution import resolve_default_runtime_profile


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


class ContextResolver(ContextResolutionSupport):
    """十段解析管线（services 应用服务；无状态，实例可跨请求复用）。"""

    def __init__(
        self,
        store: ChannelRegistryStore,
        *,
        memory_budget: int = 5,
        credential_resolver: Any | None = None,
        memory_retriever: PersonalMemoryRetriever | None = None,
        memory_recall_timeout_ms: int = 1000,
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
        # FEAT-07：recall 有限超时（默认 1000ms，可配置），不自动重试。
        self._memory_recall_timeout_ms = memory_recall_timeout_ms

    async def resolve(
        self,
        selector: ResolverSelector,
        *,
        session_id: str,
        memory_query: str | None = None,
        memory_budget: int | None = None,
        request_id: str = "",
        trace_id: str = "",
        execution_id: str = "",
    ) -> ResolveResult:
        del session_id  # session 维度由调用方承载；本管线按 (tenant, agent, user) 解析
        # TASK-006（ADR-A012 §1）：内部层禁止创建/替换身份——调用方传入三 ID，
        # 缺失/非法即 fail-closed；snapshot 与缓存一律使用传入身份。
        identity = validate_run_identity(request_id, trace_id, execution_id)
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
                # TASK-006（B-ID-02）：缓存命中复用配置内容，但身份必须用本次
                # 请求的——禁止复用前次执行身份。
                return replace(
                    result,
                    snapshot=result.snapshot.model_copy(
                        update={
                            "execution_id": identity.execution_id,
                            "trace_id": identity.trace_id,
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

        # 4. runtime：Agent.runtime_profile_ref → RuntimeProfile；未配置时走
        # ADR-A010 默认链（Tenant Default → platform-default），同名回退已废弃。
        started = time.perf_counter()
        agent_spec = AgentDefinition.model_validate(agent.spec_json)
        if agent_spec.runtime_profile_ref is not None:
            profile_id = agent_spec.runtime_profile_ref.id
            profile_version = selector.runtime_profile_version or agent_spec.runtime_profile_ref.version
            profile_row = await self._store.get(
                ResourceKind.RUNTIME_PROFILE, profile_id, tenant_id=selector.tenant_id,
                version=None if profile_version == "latest-published" else profile_version,
            )
            if profile_row is None:
                raise ContextResolutionError(code="runtime_profile_not_found", message=f"{profile_id}@{profile_version} not found", status_code=404)
        elif selector.runtime_profile_version is not None:
            # 版本 pin 依赖 ref 提供目标坐标；无 ref 的 pin 是矛盾输入，fail-closed。
            raise ContextResolutionError(
                code="runtime_profile_ref_required",
                message=(
                    "runtime_profile_version pin requires Agent.runtime_profile_ref "
                    "(same-name fallback removed per ADR-A010)"
                ),
                status_code=409,
            )
        else:
            profile_row = await resolve_default_runtime_profile(
                self._store, selector.tenant_id
            )
            if profile_row is None:
                raise ContextResolutionError(
                    code="runtime_profile_default_missing",
                    message=(
                        "no default RuntimeProfile: tenant default (default=true, published) "
                        "and platform-default both missing (ADR-A010)"
                    ),
                    status_code=409,
                )
        _stage("runtime", profile_row.version, started)

        # 5. model：ADR-A008 三层解析——AgentDefinition.model_policy →
        # ModelDefinition → ProviderDefinition。任一引用缺失 fail-closed，
        # 不回退 legacy 直引（双事实源消灭）；回退链归 ModelPolicy（归属切分），
        # 不再消费 RuntimeProfile.model_failover。
        profile_spec = RuntimeProfile.model_validate(profile_row.spec_json)
        model_refs = [
            agent_spec.model_policy.primary_model_ref,
            *agent_spec.model_policy.fallback_model_refs,
        ]
        models = [
            await self._resolve_model_definition(selector.tenant_id, ref) for ref in model_refs
        ]
        model_resolution = ModelPolicy(
            routes=[
                ResolvedModelRoute(provider_ref=item.provider_ref, model_ref=ref, model=item.name)
                for ref, item in zip(model_refs, models)
            ],
            model_timeout_ms=agent_spec.model_policy.model_timeout_ms,
            max_rounds=profile_spec.max_rounds,
            model_deadline_ms=agent_spec.model_policy.model_deadline_ms,
        )
        # ADR-A003 amend：typed pins——provider 与 model 分别 exact version pin。
        provider_versions = {
            item.provider_ref.id: item.provider_ref.version for item in models
        }
        model_versions = {ref.id: ref.version for ref in model_refs}

        # 6. profile：User Profile 版本已解析（stage 2）

        # 6. memory：PersonalMemoryRetriever recall → manifest（失败降级空 manifest）
        started = time.perf_counter()
        manifest = await self._memory_manifest(
            selector.tenant_id, platform_user_id or selector.user_id, memory_query, memory_budget,
            request_id=request_id, trace_id=trace_id,
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
            skill_instructions,
            skill_required_capabilities,
        ) = await self._resolve_capability_versions(
            selector.tenant_id, agent_spec.capabilities, selector.user_id
        )
        _stage("capability", None, started)

        # 8. credential：bindings credential_ref → versions（只存 ref→version）
        started = time.perf_counter()
        credential_versions = await self._credential_versions(selector.tenant_id, platform_user_id or selector.user_id)
        # ADR-A003 amend（TASK-005）：provider credential 选择在构建期收口——
        # 对每个 provider 冻结最终 credential_ref（运行期按此 ref 解密，不重选）。
        provider_credentials: dict[str, str] = {}
        for route in model_resolution.routes:
            provider_spec = await self._store.get(
                ResourceKind.MODEL_PROVIDER,
                route.provider_ref.id,
                tenant_id=selector.tenant_id,
                version=route.provider_ref.version,
            )
            if provider_spec is None or provider_spec.status is not ResourceStatus.PUBLISHED:
                raise ContextResolutionError(
                    code="model_provider_not_found",
                    message=f"model_provider {route.provider_ref.id}@{route.provider_ref.version} not found",
                    status_code=422,
                )
            provider_credentials[route.provider_ref.id] = await resolve_effective_credential_ref(
                self._store,
                provider_id=route.provider_ref.id,
                tenant_id=selector.tenant_id,
                user_id=selector.user_id,
                spec=provider_spec.spec_json,
            )
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
        # RULE-02 三维真值表（design/02 §3）：User/Agent/Tenant 任一维度缺失即
        # deny。无 tenant policy → tenant 维度为空集（fail-closed），不再拷贝
        # user_tools（TASK-003 返工）；policy 模式与 denied 集冻结进 snapshot，
        # 运行期 frozen_tool_policy 按模式展开（deny_only = 除 denied 外全部）。
        if not policy_configured:
            tenant_tools: set[str] = set()
            tenant_policy_mode = "unconfigured"
        elif policy_allowed:
            tenant_tools = set(policy_allowed)
            tenant_policy_mode = "allow_list"
        else:
            tenant_tools = set()
            tenant_policy_mode = "deny_only"
        if policy_denied:
            user_tools = user_tools - policy_denied
            agent_tools = agent_tools - policy_denied
            tenant_tools = tenant_tools - policy_denied

        # 10. snapshot：V2 全字段 + canonical digest
        started = time.perf_counter()
        snapshot = ExecutionSnapshot(
            execution_id=identity.execution_id,
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
                "denied_tools": sorted(policy_denied),
                "tenant_tool_policy": tenant_policy_mode,
            },
            trace_id=identity.trace_id,
            system_prompt=agent_spec.system_prompt,
            skill_instructions=skill_instructions,
            skill_required_capabilities=skill_required_capabilities,
            skill_versions=skill_versions,
            mcp_versions=mcp_versions,
            provider_versions=provider_versions,
            model_versions=model_versions,
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
            provider_credentials=provider_credentials,
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

class ContextResolverSnapshotBuilder:
    """把 ContextResolver.resolve 适配到 AgentRuntime 的 build(request) 接口。

    TASK-002：主 invoke 从 ExecutionSnapshotBuilder 切到 ContextResolver。
    request 只需暴露 tenant_id/user_id/agent_definition_id/runtime_profile_id/
    runtime_profile_version_selector/session_id（与 RequestContext 兼容）。
    """

    def __init__(self, resolver: ContextResolver) -> None:
        self._resolver = resolver

    async def build(self, request: Any) -> ExecutionSnapshot:
        # ADR-A010：Agent 是执行主坐标（persona/model/capability SoT）；
        # 与 runtime_profile_id 同名的回退已废弃，缺省 fail-closed。
        agent_id = request.agent_definition_id
        if agent_id is None:
            raise ContextResolutionError(
                code="agent_coordinate_required",
                message=(
                    "execution requires agent_definition_id "
                    "(same-name profile fallback removed per ADR-A010)"
                ),
                status_code=409,
            )
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
        result = await self._resolver.resolve(
            selector,
            session_id=request.session_id,
            request_id=getattr(request, "request_id", "") or "",
            trace_id=getattr(request, "trace_id", "") or "",
            execution_id=getattr(request, "execution_id", "") or "",
        )
        # TASK-006：resolve 已使用传入身份组装 snapshot，不再事后覆盖。
        return result.snapshot
