"""TASK-004：CapabilityTestService——测试调用与正式执行共用执行器。

RULE-CAP-06（测试路径 == 正式执行路径）：测试侧经同一 ToolExecutorRegistry
装配同一 HTTPToolExecutor，只是不经过 ToolRuntime 授权链（Console 操作员
侧显式触发，授权由调用方保证）。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from fluxion.registry import RegistryReadStore
from fluxion.resources import ModelPolicy, ResourceKind
from fluxion.resources.contracts import ExecutionSnapshot
from fluxion.resources.resource_specs import ToolDefinition
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.secrets import CredentialResolver, SecretProviderError
from fluxion.runtime.tool_executors import (
    ClientFactory,
    CredentialProvider,
    ExecutorDeps,
    ToolExecutorRegistry,
    _default_registry,
    _descriptor_for,
    credential_provider_from_resolver,
)
from fluxion.runtime.tools import ToolRuntimeError

if TYPE_CHECKING:
    from fluxion.runtime.platform_services import PlatformServiceRegistry


class CapabilityTestError(RuntimeError):
    pass


class CapabilityTestService:
    """测试执行服务（Console :test 与未来 :discover 共用）。"""

    def __init__(
        self,
        store: RegistryReadStore,
        *,
        credential_resolver: CredentialResolver | None = None,
        api_key_provider: Callable[[str], Awaitable[str | None]] | None = None,
        client_factory: ClientFactory | None = None,
        registry: ToolExecutorRegistry | None = None,
        service_registry: PlatformServiceRegistry | None = None,
    ) -> None:
        self._store = store
        self._credential_resolver = credential_resolver
        self._api_key_provider = api_key_provider
        self._client_factory = client_factory
        self._registry = registry
        self._service_registry = service_registry

    def _credential_provider(self) -> CredentialProvider | None:
        if self._credential_resolver is not None:
            return credential_provider_from_resolver(self._credential_resolver)
        provider = self._api_key_provider
        if provider is None:
            return None

        async def provide(ref: str, tenant_id: str) -> str:
            try:
                key = await provider(ref)
            except SecretProviderError as exc:
                raise ToolRuntimeError(f"凭据解析失败: {exc}") from exc
            if key is None:
                raise ToolRuntimeError("credential_resolver_missing: 凭据解析器未配置")
            return key

        return provide

    async def test_tool(
        self,
        *,
        tenant_id: str,
        tool_id: str,
        version: str | None = None,
        arguments: dict[str, object] | None = None,
        credential_ref: str | None = None,
    ) -> dict[str, object]:
        """执行一次测试调用，返回执行器原始结果字典。"""
        row = await self._store.get(
            ResourceKind.TOOL, tool_id, tenant_id=tenant_id, version=version
        )
        if row is None:
            raise CapabilityTestError(f"tool {tool_id} not found")
        definition = ToolDefinition.model_validate(row.spec_json)
        if definition.tool_kind not in ("http_api", "platform_service"):
            raise CapabilityTestError(
                f"unsupported tool kind: {definition.tool_kind}"
            )
        _, exec_descriptor = _descriptor_for(
            tool_id, definition, tenant_id, credential_ref
        )
        if self._registry is not None:
            active = self._registry
        else:
            active = _default_registry(
                ExecutorDeps(
                    credential_provider=self._credential_provider(),
                    client_factory=self._client_factory,
                )
            )
            if self._service_registry is not None:
                from fluxion.runtime.platform_services import (
                    platform_executor_factory,
                )

                active.register_kind(
                    "platform_service",
                    platform_executor_factory(
                        self._service_registry,
                        credential_provider=self._credential_provider(),
                        client_factory=self._client_factory,
                    ),
                )
        try:
            executor = active.executor_for(exec_descriptor)
        except ToolRuntimeError as exc:
            raise CapabilityTestError(str(exc)) from exc
        context = _test_context(tenant_id)
        raw = executor(context, dict(arguments or {}))
        result = await raw if inspect.isawaitable(raw) else raw
        if not isinstance(result, dict):
            raise CapabilityTestError("executor must return a result mapping")
        return {str(k): v for k, v in result.items()}


def _test_context(tenant_id: str) -> RuntimeContext:
    execution_id = uuid4().hex
    return RuntimeContext(
        request=RequestContext(
            tenant_id=tenant_id,
            user_id="console-test",
            session_id=f"test-{execution_id[:8]}",
        ),
        snapshot=ExecutionSnapshot(
            execution_id=execution_id,
            tenant_id=tenant_id,
            user_id="console-test",
            runtime_profile_id="console-test",
            runtime_profile_version="1",
            model_resolution=ModelPolicy(),
            trace_id=uuid4().hex,
        ),
    )
