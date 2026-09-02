from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from fluxion.agents import AgentDefinition
from fluxion.errors.console import ConsoleValidationError, StudioSpecValidationError
from fluxion.registry import ChannelRegistryStore
from fluxion.resources import ModelDefinition, ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.secrets import CredentialResolver, ResolvedCredential, SecretProviderError
from fluxion.services.capability_planning import CapabilityPlanningService
from fluxion.services.connection_test import ConnectionTestResult, ConnectionTestService
from fluxion.services.console_contracts import ConsoleActor, PublishValidationResult
from fluxion.services.console_resource_schema import (
    _definition_model,
    _raise_for_invalid_workflow,
    _validate_definition,
)
from fluxion.services.workflow_app import WorkflowDefinitionValidator, WorkflowValidationResult


class ConsoleResourceValidationOps:
    """资源 schema、发布前引用校验与 Provider/MCP 连接测试。"""

    _store: ChannelRegistryStore
    _workflow_validator: WorkflowDefinitionValidator
    _credential_resolver: CredentialResolver | None

    if TYPE_CHECKING:

        async def _get_exact_resource(
            self,
            kind: ResourceKind,
            resource_id: str,
            tenant_id: str,
            version: str,
        ) -> ResourceDefinition: ...

        async def get_resource(
            self,
            actor: ConsoleActor,
            kind: ResourceKind,
            resource_id: str,
            *,
            version: str | None = None,
        ) -> ResourceDefinition: ...

    async def validate_resource_version(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
        version: str,
    ) -> WorkflowValidationResult:
        resource = await self._get_exact_resource(
            kind,
            resource_id,
            actor.tenant_id,
            version,
        )
        if kind is ResourceKind.WORKFLOW:
            result = await self._workflow_validator.validate(
                tenant_id=actor.tenant_id,
                spec=resource.spec_json,
            )
            # E-C104：workflow DSL / capability 校验失败以 400 返回具体诊断。
            _raise_for_invalid_workflow(result)
            return result
        # 其余资源类型：校验失败返回 valid=false 结果（200），由调用方决定行为。
        return _validate_definition(kind, resource.spec_json)

    async def validate_publish(
        self,
        actor: ConsoleActor,
        kind: ResourceKind,
        resource_id: str,
        version: str,
    ) -> PublishValidationResult:
        """发布完整校验（remediation §14.4）：schema + 引用完整性 + 凭据可用性。

        返回结构化可操作问题清单，供前端「发布」按钮渲染；发布链自身保持
        fail-closed（invalid spec 不可发布）。
        """
        resource = await self.get_resource(actor, kind, resource_id, version=version)
        issues: list[str] = []
        # 1. schema（typed spec + secret 拒绝）
        result = _validate_definition(kind, resource.spec_json)
        if not result.valid:
            issues.extend(result.diagnostics)
        # 2. workflow 引用完整性（带能力引用存在性检查）
        if kind is ResourceKind.WORKFLOW:
            wf_result = await self._workflow_validator.validate(
                tenant_id=actor.tenant_id,
                spec=resource.spec_json,
            )
            if not wf_result.valid:
                issues.extend(wf_result.diagnostics)
        # 3. AgentDefinition 引用完整性 + Skill 依赖闭包（S-04/E-05）
        if kind is ResourceKind.AGENT_DEFINITION:
            issues.extend(
                await self._agent_reference_issues(actor.tenant_id, resource.spec_json)
            )
        if kind is ResourceKind.MODEL_DEFINITION:
            issues.extend(
                await self._model_definition_reference_issues(
                    actor.tenant_id, resource.spec_json
                )
            )
        # 4. 凭据可用性
        issues.extend(
            await self._credential_issues(actor.tenant_id, kind, resource.spec_json)
        )
        return PublishValidationResult(valid=not issues, issues=issues)

    async def _agent_reference_issues(
        self,
        tenant_id: str,
        spec: dict[str, object],
    ) -> list[str]:
        """AgentDefinition 引用完整性（S-04）：capabilities（skill/mcp）与
        model_policy 全部 ModelDefinition 必须指向已发布资源；Skill 依赖闭包经
        CapabilityPlanningService 检查（E-05，kind-aware）。
        """
        try:
            agent_spec = AgentDefinition.model_validate(spec)
        except ValidationError:
            return []  # schema 段已产出诊断，引用检查跳过
        issues: list[str] = []
        for cap in agent_spec.capabilities:
            if cap.type.value not in ("skill", "mcp"):
                continue
            kind = ResourceKind.SKILL if cap.type.value == "skill" else ResourceKind.MCP
            row = await self._store.get(
                kind,
                cap.capability_ref,
                tenant_id=tenant_id,
                version=None
                if cap.version_pin == "latest-published"
                else cap.version_pin,
            )
            if row is None:
                issues.append(
                    f"能力引用 {cap.capability_ref}@{cap.version_pin} 不可解析"
                    f"（{cap.type.value} 资源不存在）"
                )
            elif row.status is not ResourceStatus.PUBLISHED:
                issues.append(
                    f"能力引用 {cap.capability_ref}@{cap.version_pin} 未发布"
                    f"（{cap.type.value} 资源不可用于运行）"
                )
        model_refs = [
            agent_spec.model_policy.primary_model_ref,
            *agent_spec.model_policy.fallback_model_refs,
        ]
        for model_ref in model_refs:
            model_row = await self._store.get(
                ResourceKind.MODEL_DEFINITION,
                model_ref.id,
                tenant_id=tenant_id,
                version=model_ref.version,
            )
            if model_row is None:
                issues.append(f"模型定义 {model_ref.id}@{model_ref.version} 不存在")
                continue
            if model_row.status is not ResourceStatus.PUBLISHED:
                issues.append(f"模型定义 {model_ref.id}@{model_ref.version} 未发布")
                continue
            issues.extend(
                await self._model_definition_reference_issues(
                    tenant_id, model_row.spec_json
                )
            )
        plan = await CapabilityPlanningService(self._store).plan_agent_capabilities(
            tenant_id=tenant_id,
            agent_spec=agent_spec,
        )
        issues.extend(plan.missing)
        return issues

    async def _model_definition_reference_issues(
        self,
        tenant_id: str,
        spec: dict[str, object],
    ) -> list[str]:
        try:
            model_spec = ModelDefinition.model_validate(spec)
        except ValidationError:
            return []
        provider_ref = model_spec.provider_ref
        provider = await self._store.get(
            ResourceKind.MODEL_PROVIDER,
            provider_ref.id,
            tenant_id=tenant_id,
            version=provider_ref.version,
        )
        if provider is None:
            return [f"模型供应商 {provider_ref.id}@{provider_ref.version} 不存在"]
        if provider.status is not ResourceStatus.PUBLISHED:
            return [f"模型供应商 {provider_ref.id}@{provider_ref.version} 未发布"]
        return []

    async def _credential_issues(
        self,
        tenant_id: str,
        kind: ResourceKind,
        spec: dict[str, object],
    ) -> list[str]:
        """引用凭据存在性校验：MODEL_PROVIDER 的 credential_ref 必须指向已定义
        SECRET 资源，否则返回可操作问题（B-E-02）。"""
        if kind is not ResourceKind.MODEL_PROVIDER:
            return []
        credential_ref = spec.get("credential_ref")
        if not isinstance(credential_ref, str) or not credential_ref:
            return []
        # secret://{tenant}/{name}@{version} → name 即 SECRET 资源 id
        rest = (
            credential_ref[len("secret://"):]
            if credential_ref.startswith("secret://")
            else ""
        )
        name = rest.split("@")[0]
        if "/" in name:
            name = name.split("/", 1)[1]
        if not name:
            return []
        # 存在性检查（任意状态）：凭据元数据已定义即可，不要求已发布
        # （store.get(version=None) 只查 PUBLISHED，draft 阶段会误报缺失）。
        items, _total = await self._store.list_versions(
            ResourceKind.SECRET,
            name,
            tenant_id=tenant_id,
            offset=0,
            limit=1,
        )
        if not items:
            return [f"凭据 {credential_ref} 不可用：SECRET 资源 {name} 未定义"]
        return []

    async def test_model_provider_connection(
        self,
        actor: ConsoleActor,
        provider_id: str,
    ) -> ConnectionTestResult:
        """测试 Model Provider 连接（TASK-019 返工）：可达性 + 发现模型。

        凭据经装配的 CredentialResolver 注入（Secret 不进 spec/日志——规则 17）；
        resolver 未装配且 spec 引用凭据时显式报错，不发无 Authorization 请求。
        """

        async def api_key_provider(ref: str) -> str | None:
            if self._credential_resolver is None:
                raise SecretProviderError(
                    "credential_resolver_missing",
                    "凭据解析器未配置（Console 装配缺失），无法注入 Authorization",
                )
            return await self._credential_resolver.resolve(ref, tenant_id=actor.tenant_id)

        return await ConnectionTestService(
            self._store, api_key_provider=api_key_provider
        ).test_connection(
            tenant_id=actor.tenant_id,
            provider_id=provider_id,
        )

    async def test_mcp_connection(
        self,
        actor: ConsoleActor,
        mcp_id: str,
    ) -> ConnectionTestResult:
        """测试 MCP 连接（B-S-07）：握手 + 发现工具。

        凭据取 tenant binding 的 credential_ref（Console 管理上下文），经
        CredentialResolver 解析后注入 transport（stdio env / http header）。
        """
        credential, credential_ref = await self._mcp_binding_credential(actor, mcp_id)
        return await ConnectionTestService(self._store).test_mcp_connection(
            tenant_id=actor.tenant_id,
            mcp_id=mcp_id,
            credential=credential,
            credential_ref=credential_ref,
        )

    async def _mcp_binding_credential(
        self, actor: ConsoleActor, mcp_id: str
    ) -> tuple[ResolvedCredential | None, str | None]:
        """tenant binding 的 MCP 凭据（不可解析时 fail-closed 报错，不静默无凭据连接）。"""
        bindings = await self._store.list_bindings(
            subject_type="tenant",
            subject_id=actor.tenant_id,
            tenant_id=actor.tenant_id,
            resource_type=ResourceKind.MCP,
        )
        binding = next(
            (item for item in bindings if item.resource_id == mcp_id and item.enabled),
            None,
        )
        if binding is None or binding.credential_ref is None:
            return None, None
        if self._credential_resolver is None:
            raise ConsoleValidationError(
                "凭据解析器未配置（Console 装配缺失），无法解析 MCP binding 凭据"
            )
        try:
            credential = await self._credential_resolver.resolve_with_metadata(
                binding.credential_ref, tenant_id=actor.tenant_id
            )
        except SecretProviderError as exc:
            raise ConsoleValidationError(f"MCP 凭据解析失败：{exc}") from exc
        return credential, binding.credential_ref

    async def resource_schema(self, kind: ResourceKind) -> dict[str, object]:
        # ADR-012：前端表单 schema 直接取自 spec model 的 model_json_schema()，
        # 校验模型与表单结构不可能漂移。schema 是 kind 级静态数据，无租户上下文。
        model = _definition_model(kind)
        if model is None:
            raise ConsoleValidationError(f"unsupported resource type: {kind.value}")
        return {"schema": model.model_json_schema()}

    def validate_spec_shape(
        self, kind: ResourceKind, spec: dict[str, object]
    ) -> None:
        """Product API 前置 typed 校验（TASK-004 E-01）：进入 draft 前定位字段错误。

        slug=agent_definition_invalid（agents 语义），诊断含 pydantic 字段路径；
        其它 kind 同样前置（schema 驱动表单一套行为）。
        """
        result = _validate_definition(kind, spec)
        if not result.valid:
            raise StudioSpecValidationError(
                "agent_definition_invalid：" + "；".join(result.diagnostics)
            )
