"""ContextResolver 的资源查找与用户上下文辅助逻辑。"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict

from fluxion.observability.logging import emit_memory_event_log
from fluxion.resources import ModelDefinition, ResourceKind, ResourceStatus, SkillDefinition
from fluxion.resources.contracts import ExactResourceVersion, MemoryEntryRef, MemoryManifest

if TYPE_CHECKING:
    from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
    from fluxion.registry import ChannelRegistryStore
    from fluxion.registry.store import ScopedRegistryReader


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


class _SkillSpecView(SkillDefinition):
    """resolver 侧 skill spec 读取视图（extra=ignore 容忍存量 spec 扩展字段）。"""

    model_config = ConfigDict(extra="ignore")


class ContextResolutionSupport:
    """ContextResolver 的无状态辅助操作；由主解析器提供依赖。"""

    if TYPE_CHECKING:
        _store: ChannelRegistryStore
        _memory_budget: int
        _credential_resolver: Any | None
        _memory_retriever: PersonalMemoryRetriever | None
        _memory_recall_timeout_ms: int

    async def _latest_user_profile_version(
        self,
        store: ChannelRegistryStore | ScopedRegistryReader,
        tenant_id: str,
        user_id: str,
    ) -> str | None:
        row = await store.get_latest_user_profile(
            tenant_id=tenant_id, platform_user_id=user_id
        )
        return str(row["version"]) if row else None

    async def _memory_manifest(
        self,
        tenant_id: str,
        user_id: str,
        memory_query: str | None,
        memory_budget: int | None,
        *,
        request_id: str = "",
        trace_id: str = "",
    ) -> MemoryManifest:
        """经 PersonalMemoryRetriever recall；失败降级为空 manifest。

        FEAT-07：recall 经有限 timeout（默认 1000ms，可配置）包夹，不自动
        重试；异常/超时降级为 unavailable 并记脱敏 warning（错误类型 +
        request_id/trace_id 关联，不记 query 内容与异常原文）。成功空结果为
        正常空 manifest（content_hash 为空），与失败严格区分。
        """
        budget = memory_budget if memory_budget is not None else self._memory_budget
        if self._memory_retriever is None:
            return MemoryManifest(entry_refs=[], content_hash="unavailable", truncated=True)
        try:
            entries = await asyncio.wait_for(
                self._memory_retriever.recall(
                    tenant_id, user_id, query=memory_query or "", top_k=budget + 10
                ),
                timeout=self._memory_recall_timeout_ms / 1000.0,
            )
        except TimeoutError:
            emit_memory_event_log(
                event="memory.recall.degraded",
                level="warning",
                tenant_id=tenant_id,
                trace_id=trace_id or None,
                detail=f"timeout budget_ms={self._memory_recall_timeout_ms} request_id={request_id}",
            )
            return MemoryManifest(entry_refs=[], content_hash="unavailable", truncated=True)
        except Exception as exc:  # noqa: BLE001 - FEAT-07 设计要求：recall 任何失败降级 unavailable，不阻塞执行
            emit_memory_event_log(
                event="memory.recall.degraded",
                level="warning",
                tenant_id=tenant_id,
                trace_id=trace_id or None,
                detail=f"{type(exc).__name__} request_id={request_id}",
            )
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
        """user_id 无前缀时视为 platform_user_id，保留 Channel ID 回退。"""
        if user_id.startswith(("migration:", "user-")):
            return user_id
        resolved = await self._store.resolve_platform_user_by_channel_id(
            tenant_id=tenant_id, channel_user_id=user_id
        )
        return resolved or user_id

    async def _resolve_model_definition(
        self,
        store: ChannelRegistryStore | ScopedRegistryReader,
        tenant_id: str,
        model_ref: ExactResourceVersion,
    ) -> ModelDefinition:
        """加载已发布 ModelDefinition，并校验其精确 Provider 引用。"""
        definition = await store.get(
            ResourceKind.MODEL_DEFINITION,
            model_ref.id,
            tenant_id=tenant_id,
            version=model_ref.version,
        )
        if definition is None:
            raise ContextResolutionError(
                code="model_definition_not_found",
                message=f"model_definition {model_ref.id}@{model_ref.version} not found",
                status_code=422,
            )
        if definition.status is not ResourceStatus.PUBLISHED:
            raise ContextResolutionError(
                code="model_definition_not_published",
                message=f"model_definition {model_ref.id}@{model_ref.version} is not published",
                status_code=422,
            )
        model = ModelDefinition.model_validate(definition.spec_json)
        provider = await store.get(
            ResourceKind.MODEL_PROVIDER,
            model.provider_ref.id,
            tenant_id=tenant_id,
            version=model.provider_ref.version,
        )
        provider_name = f"{model.provider_ref.id}@{model.provider_ref.version}"
        if provider is None:
            raise ContextResolutionError(
                code="model_provider_not_found",
                message=f"model_provider {provider_name} not found",
                status_code=422,
            )
        if provider.status is not ResourceStatus.PUBLISHED:
            raise ContextResolutionError(
                code="model_provider_not_published",
                message=f"model_provider {provider_name} is not published",
                status_code=422,
            )
        return model

    async def _resolve_capability_versions(
        self,
        store: ChannelRegistryStore | ScopedRegistryReader,
        tenant_id: str,
        capabilities: list[Any],
        user_id: str,
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        dict[str, str],
        list[str],
        dict[str, dict[str, str]],
        dict[str, str],
    ]:
        """解析 Agent baseline 与用户扩展的 Skill/MCP published 版本。"""
        agent_skill_pins = {
            cap.capability_ref: cap.version_pin for cap in capabilities if cap.type == "skill"
        }
        bindings = await store.list_bindings(
            subject_type="user",
            subject_id=user_id,
            tenant_id=tenant_id,
            resource_type=ResourceKind.SKILL,
        )
        binding_granted = {binding.resource_id for binding in bindings}
        effective_skill_pins = dict(agent_skill_pins)
        for binding in bindings:
            current_pin = effective_skill_pins.get(binding.resource_id)
            if current_pin is None or current_pin == "latest-published":
                effective_skill_pins[binding.resource_id] = binding.resource_version_selector

        skill_versions: dict[str, str] = {}
        mcp_versions: dict[str, str] = {}
        skill_instructions: dict[str, str] = {}
        required_capabilities: set[str] = set()
        for ref, version_pin in effective_skill_pins.items():
            row = await store.get(
                ResourceKind.SKILL,
                ref,
                tenant_id=tenant_id,
                version=None if version_pin == "latest-published" else version_pin,
            )
            if row is None:
                if ref in agent_skill_pins:
                    raise ContextResolutionError(
                        code="skill_version_not_found",
                        message=f"skill {ref}@{version_pin} not found",
                        status_code=404,
                    )
                continue
            # TASK-006：Draft Skill 不进快照（SKL-01 语义：声明草稿仅排除，
            # 解析继续；显式 /skill 调用落空到 skill_not_available）。
            if row.status is not ResourceStatus.PUBLISHED:
                continue
            parsed = _SkillSpecView.model_validate(row.spec_json)
            if parsed.visibility == "private" and ref not in binding_granted:
                continue
            skill_versions[ref] = row.version
            if parsed.instructions.strip():
                skill_instructions[ref] = parsed.instructions.strip()
            required_capabilities.update(
                item for item in parsed.required_capabilities if item.strip()
            )

        for cap in capabilities:
            if cap.type != "mcp":
                continue
            row = await store.get(
                ResourceKind.MCP,
                cap.capability_ref,
                tenant_id=tenant_id,
                version=None if cap.version_pin == "latest-published" else cap.version_pin,
            )
            if row is None:
                continue
            # TASK-006：Draft MCP 不进快照（与 Skill 同语义；显式调用落空拒绝）。
            if row.status is not ResourceStatus.PUBLISHED:
                continue
            mcp_versions[cap.capability_ref] = row.version

        # TASK-003：各 bound MCP 精确版本 approved + enabled 策略冻结
        # {tool_name: schema_hash}（纯配置读，scope 内）。
        mcp_tool_policies: dict[str, dict[str, str]] = {}
        for mcp_id, mcp_version in mcp_versions.items():
            policies = await store.list_mcp_tool_policies(
                tenant_id=tenant_id,
                mcp_id=mcp_id,
                mcp_version=int(mcp_version),
            )
            frozen = {
                p.tool_name: p.schema_hash for p in policies if p.enabled
            }
            if frozen:
                mcp_tool_policies[mcp_id] = frozen

        # TASK-009：已冻结 Skill 的 artifact_uri 钉入 artifact_refs（scope 内配置读）。
        skill_artifacts: dict[str, str] = {}
        for skill_id, version in skill_versions.items():
            try:
                skill_row = await store.get_capability_skill(
                    tenant_id=tenant_id, skill_id=skill_id, version=int(version)
                )
            except (ValueError, TypeError):
                continue
            if skill_row is not None and skill_row.artifact_uri:
                skill_artifacts[skill_id] = skill_row.artifact_uri

        return (
            skill_versions,
            mcp_versions,
            skill_instructions,
            sorted(required_capabilities),
            mcp_tool_policies,
            skill_artifacts,
        )

    async def _credential_binding_refs(
        self,
        store: ChannelRegistryStore | ScopedRegistryReader,
        tenant_id: str,
        user_id: str,
    ) -> list[str]:
        """用户 binding 上的凭据引用（纯配置读；scoped 一致读内调用）。"""
        bindings = await store.list_bindings(
            subject_type="user", subject_id=user_id, tenant_id=tenant_id
        )
        return list(dict.fromkeys(b.credential_ref for b in bindings if b.credential_ref))

    async def _credential_versions_from_refs(
        self, refs: list[str], tenant_id: str
    ) -> dict[str, str]:
        """凭据版本解析（外部 SecretStore I/O；必须在 scoped read 之外调用）。"""
        versions: dict[str, str] = {}
        for ref in refs:
            if self._credential_resolver is None:
                versions[ref] = "1"
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


def _short_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
