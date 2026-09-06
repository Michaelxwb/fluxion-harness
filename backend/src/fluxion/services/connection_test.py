"""连接测试服务（remediation §16 / TASK-019 / TASK-019 返工）。

Provider 连接测试：对 OpenAI-compatible `{base_url}/models` 轻量探测，经注入的
api_key_provider 携带凭据（生产路径由 Console 装配 SecretStore resolver）；
MCP 连接测试：经 OfficialMCPClient 握手 + list_tools 发现工具。凭据/端点错误
返回可操作信息，不静默失败。client_factory / api_key_provider 可注入
（测试用 httpx.MockTransport / 桩 resolver）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from fluxion.registry import RegistryStore
from fluxion.resources import ProviderDefinition, ResourceDefinition, ResourceKind
from fluxion.runtime.mcp import (
    MCPRuntimeError,
    MCPTimeoutError,
    OfficialMCPClient,
    mcp_server_config,
)
from fluxion.runtime.secrets import ResolvedCredential, SecretProviderError


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    reachable: bool
    discovered_models: list[str] = field(default_factory=list)
    discovered_tools: list[str] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None
    body_excerpt: str | None = None


ClientFactory = Callable[[], httpx.AsyncClient]
ApiKeyProvider = Callable[[str], Awaitable[str | None]]


class ConnectionTestService:
    """Provider / MCP 连接测试（B-S-07 / B-E-04）。"""

    def __init__(
        self,
        store: RegistryStore,
        *,
        client_factory: ClientFactory | None = None,
        api_key_provider: ApiKeyProvider | None = None,
    ) -> None:
        self._store = store
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10.0))
        self._api_key_provider = api_key_provider

    async def test_tool_call(
        self,
        *,
        tenant_id: str,
        tool_id: str,
        api_key_provider: ApiKeyProvider | None = None,
    ) -> ConnectionTestResult:
        """golden-path-closure TASK-017（§8.4）：Tool Test Call（真实出站）。

        - http_api：按 spec.method/url/headers 真实请求；timeout 取 spec.timeout_ms
          （规则 18：显式超时，无无限等待）；credential_ref 经 resolver 注入
          Authorization（规则 17：Secret 不进日志/trace）。
        - platform_service：本仓无 Platform Service registry，诚实返回不支持
          （不伪造成功）。
        """
        tool = await self._latest_resource(ResourceKind.TOOL, tool_id, tenant_id)
        if tool is None:
            return ConnectionTestResult(reachable=False, error=f"tool {tool_id} not found")
        from fluxion.resources.resource_specs import ToolDefinition

        spec = ToolDefinition.model_validate(tool.spec_json)
        if spec.tool_kind == "platform_service":
            return ConnectionTestResult(
                reachable=False,
                error="platform_service 类型暂不支持 Test Call（无 Platform Service registry）",
            )
        assert spec.url is not None
        headers = dict(spec.headers)
        if spec.credential_ref is not None:
            if api_key_provider is None:
                return ConnectionTestResult(
                    reachable=False,
                    error="credential_resolver_missing: 凭据解析器未配置，无法注入 Authorization",
                )
            try:
                key = await api_key_provider(spec.credential_ref)
            except SecretProviderError as exc:
                return ConnectionTestResult(reachable=False, error=f"凭据解析失败: {exc}")
            if key is not None:
                headers["Authorization"] = f"Bearer {key}"
        try:
            async with self._client_factory() as client:
                response = await client.request(
                    spec.method,
                    spec.url,
                    headers=headers,
                    timeout=spec.timeout_ms / 1000,
                )
        except httpx.TimeoutException:
            return ConnectionTestResult(reachable=False, error=f"timeout after {spec.timeout_ms}ms")
        except httpx.HTTPError as exc:
            return ConnectionTestResult(reachable=False, error=f"request failed: {exc}")
        body = response.text[:200]
        return ConnectionTestResult(
            reachable=response.is_success,
            status_code=response.status_code,
            body_excerpt=body,
            error=None if response.is_success else f"HTTP {response.status_code}",
        )

    async def _latest_resource(
        self, kind: ResourceKind, resource_id: str, tenant_id: str
    ) -> ResourceDefinition | None:
        # 任意状态读取（store.get(version=None) 只查 PUBLISHED，draft 阶段测试连接会误报缺失）
        items, _total = await self._store.list_versions(
            kind,
            resource_id,
            tenant_id=tenant_id,
            offset=0,
            limit=1,
        )
        return items[0] if items else None

    async def test_connection(self, *, tenant_id: str, provider_id: str) -> ConnectionTestResult:
        provider = await self._latest_resource(
            ResourceKind.MODEL_PROVIDER, provider_id, tenant_id
        )
        if provider is None:
            return ConnectionTestResult(reachable=False, error=f"Provider {provider_id} 不存在")
        spec = ProviderDefinition.model_validate(provider.spec_json)
        try:
            api_key = await self._provider_api_key(spec.credential_ref)
        except SecretProviderError as exc:
            return ConnectionTestResult(reachable=False, error=f"凭据解析失败：{exc}")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    f"{spec.base_url.rstrip('/')}/models",
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                models = [
                    str(item["id"])
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and "id" in item
                ]
                return ConnectionTestResult(reachable=True, discovered_models=models)
        except httpx.HTTPStatusError as exc:
            return ConnectionTestResult(
                reachable=False,
                error=f"HTTP {exc.response.status_code}：凭据或端点错误",
            )
        except httpx.TimeoutException:
            return ConnectionTestResult(reachable=False, error="连接超时")
        except httpx.HTTPError as exc:
            return ConnectionTestResult(reachable=False, error=str(exc))

    async def test_mcp_connection(
        self,
        *,
        tenant_id: str,
        mcp_id: str,
        credential: ResolvedCredential | None = None,
        credential_ref: str | None = None,
    ) -> ConnectionTestResult:
        """MCP 连接测试：握手 + list_tools（凭据经调用方按 binding 解析后注入，
        Secret 不进 spec——规则 17）。"""
        resource = await self._latest_resource(ResourceKind.MCP, mcp_id, tenant_id)
        if resource is None:
            return ConnectionTestResult(reachable=False, error=f"MCP {mcp_id} 不存在")
        config = mcp_server_config(resource, credential, credential_ref)
        try:
            tools = await OfficialMCPClient(config).list_tools()
        except MCPTimeoutError as exc:
            return ConnectionTestResult(reachable=False, error=f"连接超时：{exc}")
        except MCPRuntimeError as exc:
            return ConnectionTestResult(reachable=False, error=str(exc))
        return ConnectionTestResult(
            reachable=True,
            discovered_tools=[tool.name for tool in tools],
        )

    async def _provider_api_key(self, credential_ref: str | None) -> str | None:
        if not credential_ref or self._api_key_provider is None:
            return None
        return await self._api_key_provider(credential_ref)
