from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from fluxion.observability.tracing import traced_scope
from fluxion.plugins.contracts import (
    ModelProvider,
    ModelProviderError,
    ModelProviderRegistryProtocol,
    ModelRequest,
    ModelResponse,
)
from fluxion.plugins.model_provider import OpenAICompatibleHTTPModelProvider
from fluxion.registry import RegistryReadStore, ScopedRegistryReader
from fluxion.resources import ResourceBinding, ResourceKind, ResourceStatus
from fluxion.runtime.secrets import CredentialResolver, SecretProviderError


class RegistryModelProviderError(ModelProviderError):
    code = "registry_model_provider_error"


class ScopedModelProviderResolver:
    """execution-scoped Provider Resolver（TASK-010）。

    包装 service-level registry，叠加本次 execution 的 store-backed provider，
    不 mutate 共享 registry（去跨执行/跨租户 provider 累积与泄漏）。
    """

    def __init__(self, base: ModelProviderRegistryProtocol) -> None:
        self._base = base
        self._scoped: dict[str, ModelProvider] = {}

    def register_scoped(self, provider_id: str, provider: ModelProvider) -> None:
        self._scoped[provider_id] = provider

    def resolve(self, provider_id: str) -> ModelProvider:
        scoped = self._scoped.get(provider_id)
        return scoped if scoped is not None else self._base.resolve(provider_id)


class RegistryOpenAIModelProvider:
    def __init__(
        self,
        provider_id: str,
        store: RegistryReadStore,
        credential_resolver: CredentialResolver | None,
    ) -> None:
        self._provider_id = provider_id
        self._store = store
        self._credential_resolver = credential_resolver

    async def complete(self, request: ModelRequest) -> ModelResponse:
        # O503（TASK-008）：Model span 经 traced_scope（model 名/供应商入 attributes）
        async with traced_scope(
            "model.complete",
            attributes={
                "fluxion.model_provider_id": self._provider_id,
                "model": request.model or "",
            },
        ):
            provider = await self._resolve_provider(request)
            return await provider.complete(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        async with traced_scope(
            "model.stream",
            attributes={
                "fluxion.model_provider_id": self._provider_id,
                "model": request.model or "",
            },
        ):
            provider = await self._resolve_provider(request)
            async for token in provider.stream(request):
                yield token

    async def _resolve_provider(
        self, request: ModelRequest
    ) -> OpenAICompatibleHTTPModelProvider:
        tenant_id = _required_context(request.tenant_id, "tenant_id")
        version = _required_context(request.provider_version, "provider_version")
        resource = await self._store.get(
            ResourceKind.MODEL_PROVIDER,
            self._provider_id,
            tenant_id=tenant_id,
            version=version,
        )
        if resource is None or resource.status is not ResourceStatus.PUBLISHED:
            raise RegistryModelProviderError("model provider definition not found")
        _validate_protocol(resource.spec_json)
        # ADR-A003 amend（TASK-005）：运行期只按冻结的 credential_ref 解密，
        # 不重新选择（选择链在 Snapshot 构建期收口）。
        if request.credential_ref is None:
            raise RegistryModelProviderError("model request credential_ref is required")
        credential = await self._credential(request.credential_ref, tenant_id=tenant_id)
        return _provider_from_spec(self._provider_id, resource.spec_json, credential)

    async def _credential(self, ref: str, *, tenant_id: str) -> str:
        if self._credential_resolver is None:
            raise RegistryModelProviderError("model credential resolver is not configured")
        try:
            return await self._credential_resolver.resolve(ref, tenant_id=tenant_id)
        except SecretProviderError as exc:
            raise RegistryModelProviderError(
                f"provider_credential_unresolvable: credential {ref} "
                f"unavailable for provider {self._provider_id}"
            ) from exc


async def resolve_effective_credential_ref(
    store: RegistryReadStore | ScopedRegistryReader,
    *,
    provider_id: str,
    tenant_id: str,
    user_id: str,
    spec: Mapping[str, object],
) -> str:
    """EffectiveCredential 单链的选择段（只选 ref 不解密，供 Snapshot 构建期收口）。

    User Binding ?? Tenant Binding ?? ProviderDefinition.credential_ref。
    Binding 是 override 不是运行前提；`credential_ref=None` 的 binding 跳过。
    与 RegistryOpenAIModelProvider 的语义一致（ADR-A008 amend 方案 B）。
    """
    user_bindings = await store.list_bindings(
        subject_type="user",
        subject_id=user_id,
        tenant_id=tenant_id,
        resource_type=ResourceKind.MODEL_PROVIDER,
    )
    tenant_bindings = await store.list_bindings(
        subject_type="tenant",
        subject_id=tenant_id,
        tenant_id=tenant_id,
        resource_type=ResourceKind.MODEL_PROVIDER,
    )
    override = next(
        (
            item
            for item in [*user_bindings, *tenant_bindings]
            if item.resource_id == provider_id and item.enabled and item.credential_ref is not None
        ),
        None,
    )
    if override is not None:
        return override.credential_ref
    return _required_string(spec, "credential_ref")


def _provider_from_spec(
    provider_id: str,
    spec: Mapping[str, object],
    credential: str | None,
) -> OpenAICompatibleHTTPModelProvider:
    # 105 P1-01（TASK-003）隔离声明：此处的 request_timeout_ms/max_retries 是
    # ModelProvider 自身连接语义，与已删除的 RuntimeProfile 幽灵字段同名不同义，保留不动。
    return OpenAICompatibleHTTPModelProvider(
        provider_id=provider_id,
        api_base_url=_required_string(spec, "base_url"),
        model=_optional_string(spec, "default_model") or "",
        timeout_seconds=_positive_int(spec.get("request_timeout_ms"), 60_000) / 1000,
        api_key=credential,
        max_retries=_non_negative_int(spec.get("max_retries"), 1),
    )


def _validate_protocol(spec: Mapping[str, object]) -> None:
    if spec.get("protocol") != "openai-compatible":
        raise RegistryModelProviderError("unsupported model provider protocol")


def _required_context(value: str | None, field: str) -> str:
    if value is None or not value.strip():
        raise RegistryModelProviderError(f"model request {field} is required")
    return value


def _required_string(spec: Mapping[str, object], field: str) -> str:
    value = spec.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegistryModelProviderError(f"model provider {field} is required")
    return value


def _optional_string(spec: Mapping[str, object], field: str) -> str | None:
    value = spec.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RegistryModelProviderError(f"model provider {field} must be a string")
    return value


def _positive_int(value: object, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value <= 0:
        raise RegistryModelProviderError("request_timeout_ms must be positive")
    return value


def _non_negative_int(value: object, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value < 0:
        raise RegistryModelProviderError("max_retries must be non-negative")
    return value
