from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from fluxion.observability.tracing import traced_scope
from fluxion.plugins.contracts import ModelProviderError, ModelRequest, ModelResponse
from fluxion.plugins.model_provider import OpenAICompatibleHTTPModelProvider
from fluxion.registry import RegistryReadStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, SecretProviderError


class RegistryModelProviderError(ModelProviderError):
    code = "registry_model_provider_error"


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
        user_id = _required_context(request.user_id, "user_id")
        version = _required_context(request.provider_version, "provider_version")
        resource = await self._store.get(
            ResourceKind.PLUGIN,
            self._provider_id,
            tenant_id=tenant_id,
            version=version,
        )
        if resource is None:
            raise RegistryModelProviderError("model provider definition not found")
        _validate_protocol(resource.spec_json)
        binding = await self._binding(tenant_id=tenant_id, user_id=user_id)
        credential = await self._credential(binding)
        return _provider_from_spec(self._provider_id, resource.spec_json, credential)

    async def _binding(self, *, tenant_id: str, user_id: str) -> ResourceBinding:
        user_bindings = await self._store.list_bindings(
            subject_type="user",
            subject_id=user_id,
            tenant_id=tenant_id,
            resource_type=ResourceKind.PLUGIN,
        )
        tenant_bindings = await self._store.list_bindings(
            subject_type="tenant",
            subject_id=tenant_id,
            tenant_id=tenant_id,
            resource_type=ResourceKind.PLUGIN,
        )
        binding = next(
            (
                item
                for item in [*user_bindings, *tenant_bindings]
                if item.resource_id == self._provider_id and item.enabled
            ),
            None,
        )
        if binding is None:
            raise RegistryModelProviderError("model provider binding not found")
        return binding

    async def _credential(self, binding: ResourceBinding) -> str | None:
        if binding.credential_ref is None:
            return None
        if self._credential_resolver is None:
            raise RegistryModelProviderError("model credential resolver is not configured")
        try:
            return await self._credential_resolver.resolve(
                binding.credential_ref, tenant_id=binding.tenant_id
            )
        except SecretProviderError as exc:
            raise RegistryModelProviderError("model credential is unavailable") from exc


def _provider_from_spec(
    provider_id: str,
    spec: Mapping[str, object],
    credential: str | None,
) -> OpenAICompatibleHTTPModelProvider:
    return OpenAICompatibleHTTPModelProvider(
        provider_id=provider_id,
        api_base_url=_required_string(spec, "base_url"),
        model=_required_string(spec, "model"),
        timeout_seconds=_positive_int(spec.get("request_timeout_ms"), 60_000) / 1000,
        api_key=credential,
        max_retries=_non_negative_int(spec.get("max_retries"), 1),
    )


def _validate_protocol(spec: Mapping[str, object]) -> None:
    if spec.get("plugin_type") != "model_provider":
        raise RegistryModelProviderError("plugin is not a model provider")
    if spec.get("protocol") != "openai_compatible":
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
