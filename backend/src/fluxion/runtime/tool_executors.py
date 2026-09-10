"""TASK-004：Registry Tool 执行器（HTTP）+ kind 注册表 + 装配。

- ToolExecutorRegistry：按 ToolDefinition.tool_kind 找执行器工厂。
- HTTPToolExecutor：method/url/headers + 凭据注入 + 显式超时 + 有界重试
  （仅幂等方法连接失败重试一次）+ 响应解析/output_schema 校验/错误归一/Trace。
- prepare_registry_tools：ExecutionSession 装配步——snapshot effective
  tools → recall pinned spec → user binding credential → register。
- CapabilityTestService（services/capability_test_service.py）与正式执行
  共用同一注册表与执行器（RULE-CAP-06：测试路径 == 正式执行路径）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import jsonschema

from fluxion.registry import RegistryReadStore
from fluxion.resources.resource_specs import ToolDefinition
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.secrets import CredentialResolver, SecretProviderError
from fluxion.runtime.tools import (
    ToolDescriptor,
    ToolExecutor,
    ToolRuntime,
    ToolRuntimeError,
)

if TYPE_CHECKING:
    from fluxion.runtime.platform_services import PlatformServiceRegistry


@dataclass(frozen=True, slots=True)
class ToolExecutionDescriptor:
    """注册用描述（ToolDefinition + Binding 凭据 + 租户）。"""

    tool_id: str
    definition: ToolDefinition
    tenant_id: str
    credential_ref: str | None = None


ClientFactory = Callable[[], httpx.AsyncClient]
ExecutorFactory = Callable[[ToolExecutionDescriptor, "ExecutorDeps"], ToolExecutor]
# 凭据提供：(credential_ref, tenant_id) -> 明文。运行期由 CredentialResolver
# 包装；测试期可由 ApiKeyProvider 适配。None = 未配置（有 ref 即明确失败）。
CredentialProvider = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    """执行器依赖（注册表持有；测试与运行共用同一实例）。"""

    credential_provider: CredentialProvider | None = None
    client_factory: ClientFactory | None = None


def credential_provider_from_resolver(
    resolver: CredentialResolver,
) -> CredentialProvider:
    async def provide(ref: str, tenant_id: str) -> str:
        try:
            return await resolver.resolve(ref, tenant_id=tenant_id)
        except SecretProviderError as exc:
            raise ToolRuntimeError(f"凭据解析失败: {exc}") from exc

    return provide


class ToolExecutorRegistry:
    """按 kind 分发执行器工厂（默认注册 http_api；platform_service 见 TASK-008）。"""

    def __init__(self, deps: ExecutorDeps | None = None) -> None:
        self._deps = deps or ExecutorDeps()
        self._factories: dict[str, ExecutorFactory] = {}

    def register_kind(self, kind: str, factory: ExecutorFactory) -> None:
        self._factories[kind] = factory

    def executor_for(self, descriptor: ToolExecutionDescriptor) -> ToolExecutor:
        factory = self._factories.get(descriptor.definition.tool_kind)
        if factory is None:
            raise ToolRuntimeError(
                f"unsupported tool kind: {descriptor.definition.tool_kind}"
            )
        return factory(descriptor, self._deps)


def _default_registry(deps: ExecutorDeps | None = None) -> ToolExecutorRegistry:
    registry = ToolExecutorRegistry(deps)
    registry.register_kind("http_api", _http_executor_factory)
    return registry


def _http_executor_factory(
    descriptor: ToolExecutionDescriptor, deps: ExecutorDeps
) -> ToolExecutor:
    return HTTPToolExecutor(descriptor, deps)


class HTTPToolExecutor:
    """HTTP API Tool 执行器（V4 §40 职责表）。"""

    def __init__(
        self, descriptor: ToolExecutionDescriptor, deps: ExecutorDeps
    ) -> None:
        self._descriptor = descriptor
        self._deps = deps

    async def __call__(
        self, context: RuntimeContext, arguments: dict[str, object]
    ) -> dict[str, object]:
        spec = self._descriptor.definition
        assert spec.url is not None
        headers = dict(spec.headers)
        if self._descriptor.credential_ref is not None:
            provider = self._deps.credential_provider
            if provider is None:
                raise ToolRuntimeError("credential_resolver_missing: 凭据解析器未配置")
            key = await provider(self._descriptor.credential_ref, self._descriptor.tenant_id)
            headers["Authorization"] = f"Bearer {key}"
        method = spec.method.upper()
        timeout = spec.timeout_ms / 1000
        client_factory = self._deps.client_factory or (
            lambda: httpx.AsyncClient(timeout=timeout)
        )
        # 有界重试：仅幂等方法（GET/DELETE）连接/超时失败重试一次。
        attempts = 2 if method in ("GET", "DELETE") else 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                async with client_factory() as client:
                    if method in ("GET", "DELETE"):
                        response = await client.request(
                            method, spec.url, headers=headers,
                            params={k: str(v) for k, v in arguments.items()},
                            timeout=timeout,
                        )
                    else:
                        response = await client.request(
                            method, spec.url, headers=headers,
                            json=dict(arguments), timeout=timeout,
                        )
                break
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                continue
        else:
            raise ToolRuntimeError(
                f"tool_http_unreachable: {spec.url} after {attempts} attempts ({last_error})"
            )
        if not response.is_success:
            raise ToolRuntimeError(
                f"tool_http_error: HTTP {response.status_code} from {spec.url}"
            )
        payload = _parse_response(response)
        _validate_output(spec, payload)
        context.emit(
            "tool.http_called",
            {
                "tool_id": self._descriptor.tool_id,
                "method": method,
                "status_code": response.status_code,
            },
        )
        return payload


def _parse_response(response: httpx.Response) -> dict[str, object]:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        data = response.json()
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
        return {"result": data}
    return {"text": response.text[:4000]}


def _validate_output(spec: ToolDefinition, payload: Mapping[str, object]) -> None:
    if not spec.output_schema:
        return
    try:
        jsonschema.validate(dict(payload), spec.output_schema)
    except jsonschema.ValidationError as exc:
        raise ToolRuntimeError(f"tool_output_schema_invalid: {exc.message}") from exc


def _descriptor_for(
    tool_id: str,
    definition: ToolDefinition,
    tenant_id: str,
    credential_ref: str | None,
) -> tuple[ToolDescriptor, ToolExecutionDescriptor]:
    governance = definition.governance
    risk = governance.get("risk_level")
    operation = governance.get("operation")
    return (
        ToolDescriptor(
            tool_id=tool_id,
            capability_id=f"tool.{tool_id}",
            name=definition.name,
            parameters_schema=dict(definition.input_schema) or None,
            external_dependency=True,
            credential_ref=credential_ref,
            risk_level=str(risk) if isinstance(risk, str) else "low",
            operation="command" if operation == "command" else "query",
            side_effect=bool(governance.get("side_effect", False)),
        ),
        ToolExecutionDescriptor(
            tool_id=tool_id,
            definition=definition,
            tenant_id=tenant_id,
            credential_ref=credential_ref,
        ),
    )


async def prepare_registry_tools(
    context: RuntimeContext,
    tool_runtime: ToolRuntime,
    *,
    store: RegistryReadStore,
    credential_resolver: CredentialResolver | None = None,
    client_factory: ClientFactory | None = None,
    registry: ToolExecutorRegistry | None = None,
    service_registry: PlatformServiceRegistry | None = None,
) -> set[str]:
    """ExecutionSession 装配步：snapshot effective tools → 注册执行器。

    - spec recall 最新 published（version pin 冻结见 TASK-003 注记：当前
      effective_capability.tools 仅承载 capability_ref，pin 由 agent 声明侧
      持有；published 不可变保证同一 ref 可重现）。
    - credential 取用户 Binding（binding.credential_ref），无则 None。
    """
    from fluxion.resources import ResourceKind

    tenant_id = context.snapshot.tenant_id
    user_id = context.snapshot.user_id
    refs = list(getattr(context.snapshot.effective_capability, "tools", []) or [])
    bindings = await store.list_bindings(
        subject_type="user",
        subject_id=user_id,
        tenant_id=tenant_id,
        resource_type=ResourceKind.TOOL,
    )
    binding_cred = {b.resource_id: b.credential_ref for b in bindings}
    provider = (
        credential_provider_from_resolver(credential_resolver)
        if credential_resolver is not None
        else None
    )
    if registry is None:
        registry = _default_registry(
            ExecutorDeps(
                credential_provider=provider,
                client_factory=client_factory,
            )
        )
        # TASK-008：platform_service kind 接入（registry 显式传入时由调用方装配）。
        if service_registry is not None:
            from fluxion.runtime.platform_services import platform_executor_factory

            registry.register_kind(
                "platform_service",
                platform_executor_factory(
                    service_registry,
                    credential_provider=provider,
                    client_factory=client_factory,
                ),
            )
    active = registry
    registered: set[str] = set()
    for ref in refs:
        row = await store.get(
            ResourceKind.TOOL, ref, tenant_id=tenant_id, version=None
        )
        if row is None or row.status.value != "published":
            continue
        definition = ToolDefinition.model_validate(row.spec_json)
        if definition.tool_kind not in ("http_api", "platform_service"):
            continue
        descriptor, exec_descriptor = _descriptor_for(
            ref, definition, tenant_id, binding_cred.get(row.id)
        )
        try:
            executor = active.executor_for(exec_descriptor)
        except ToolRuntimeError:
            continue
        tool_runtime.register(descriptor, executor)
        registered.add(ref)
    return registered
