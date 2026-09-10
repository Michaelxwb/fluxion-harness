"""TASK-008：Platform Service 注册表 + 执行器。

首版范围：静态注册（service_name → base_url + operations）；K8s Service DNS
以 base_url 直通（httpx 解析，不另实现应用层 DNS RR，V4 §41）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from fluxion.resources.resource_specs import ToolDefinition
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tool_executors import (
    ClientFactory,
    CredentialProvider,
    ExecutorFactory,
    ToolExecutionDescriptor,
    _parse_response,
    _validate_output,
)
from fluxion.runtime.tools import ToolExecutor, ToolRuntimeError


@dataclass(frozen=True, slots=True)
class PlatformServiceEntry:
    """单个平台服务条目（base_url 可为 K8s Service DNS 名）。"""

    service_name: str
    base_url: str
    operations: dict[str, str]


class PlatformServiceRegistry:
    """平台服务目录（静态注册；未注册即 fail-closed）。"""

    def __init__(self) -> None:
        self._services: dict[str, PlatformServiceEntry] = {}

    def register(
        self,
        service_name: str,
        *,
        base_url: str,
        operations: dict[str, str] | None = None,
    ) -> None:
        if not service_name.strip():
            raise ValueError("service_name is required")
        if not base_url.strip():
            raise ValueError("base_url is required")
        self._services[service_name] = PlatformServiceEntry(
            service_name=service_name,
            base_url=base_url.rstrip("/"),
            operations=dict(operations or {}),
        )

    def resolve(self, service_name: str, operation: str) -> tuple[str, str]:
        """返回 (base_url, path)；未注册的服务/操作即错。"""
        entry = self._services.get(service_name)
        if entry is None:
            raise ToolRuntimeError(f"platform service not registered: {service_name}")
        path = entry.operations.get(operation)
        if path is None:
            raise ToolRuntimeError(
                f"platform operation not registered: {service_name}/{operation}"
            )
        return entry.base_url, path


@dataclass(slots=True)
class PlatformServiceExecutor:
    """Platform Service 执行器（V4 §41 职责表：发现/映射/超时/重试/归一）。"""

    descriptor: ToolExecutionDescriptor
    services: PlatformServiceRegistry
    credential_provider: CredentialProvider | None = None
    client_factory: ClientFactory | None = None
    _attempts: list[str] = field(default_factory=list, init=False, repr=False)

    async def __call__(
        self, context: RuntimeContext, arguments: dict[str, object]
    ) -> dict[str, object]:
        spec: ToolDefinition = self.descriptor.definition
        if not spec.service_name or not spec.operation:
            raise ToolRuntimeError("platform_service 缺少 service_name/operation")
        base_url, path = self.services.resolve(spec.service_name, spec.operation)
        url = f"{base_url}{path if path.startswith('/') else '/' + path}"
        headers: dict[str, str] = {}
        if self.descriptor.credential_ref is not None:
            if self.credential_provider is None:
                raise ToolRuntimeError("credential_resolver_missing: 凭据解析器未配置")
            key = await self.credential_provider(
                self.descriptor.credential_ref, self.descriptor.tenant_id
            )
            headers["Authorization"] = f"Bearer {key}"
        timeout = spec.timeout_ms / 1000
        client_factory = self.client_factory or (
            lambda: httpx.AsyncClient(timeout=timeout)
        )
        try:
            async with client_factory() as client:
                response = await client.request(
                    "POST", url, headers=headers, json=dict(arguments),
                    timeout=timeout,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ToolRuntimeError(f"platform_service_unreachable: {url} ({exc})") from exc
        except httpx.HTTPError as exc:
            raise ToolRuntimeError(f"platform_service_error: {exc}") from exc
        if not response.is_success:
            raise ToolRuntimeError(
                f"platform_service_error: HTTP {response.status_code} from {url}"
            )
        payload = _parse_response(response)
        _validate_output(spec, payload)
        context.emit(
            "tool.platform_called",
            {
                "tool_id": self.descriptor.tool_id,
                "service": spec.service_name,
                "operation": spec.operation,
                "status_code": response.status_code,
            },
        )
        return payload


def platform_executor_factory(
    services: PlatformServiceRegistry,
    credential_provider: CredentialProvider | None = None,
    client_factory: ClientFactory | None = None,
) -> ExecutorFactory:
    """ToolExecutorRegistry 工厂适配（TASK-008 接入点）。"""

    def factory(
        descriptor: ToolExecutionDescriptor, deps: object
    ) -> ToolExecutor:
        _ = deps
        return PlatformServiceExecutor(
            descriptor,
            services,
            credential_provider=credential_provider,
            client_factory=client_factory,
        )

    return factory
