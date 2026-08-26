from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx2
from mcp import Client, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from fluxion.registry import RegistryReadStore
from fluxion.resources import (
    MCPDefinition,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
)
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.mcp_pool import (
    MCPClient,
    MCPClientPool,
    MCPHTTPClientPool,
    MCPHTTPPoolKey,
)
from fluxion.runtime.secrets import CredentialResolver, ResolvedCredential, SecretProviderError
from fluxion.runtime.tools import ToolDescriptor, ToolExecutor, ToolRuntime

__all__ = [
    "MCPClient",
    "MCPClientPool",
    "MCPDiscoveredTool",
    "MCPHTTPClientPool",
    "MCPHTTPPoolKey",
    "MCPRuntimeError",
    "MCPServerConfig",
    "MCPTimeoutError",
    "MCPToolCallError",
    "MCPToolCallResult",
    "MCPTransportError",
    "OfficialMCPClient",
    "RegistryMCPRuntime",
    "mcp_tool_id",
]


class MCPRuntimeError(RuntimeError):
    code = "mcp_runtime_error"


class MCPTransportError(MCPRuntimeError):
    code = "mcp_transport_error"


class MCPTimeoutError(MCPRuntimeError):
    code = "mcp_timeout"


class MCPToolCallError(MCPRuntimeError):
    code = "mcp_tool_call_error"


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    transport: str
    server_uri: str
    timeout_ms: int
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    cwd: Path | None = None
    url: str | None = None
    headers: Mapping[str, str] | None = None
    allowed_tools: frozenset[str] = frozenset()
    credential_ref: str | None = None
    credential_version: str = "none"


@dataclass(frozen=True, slots=True)
class MCPDiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class MCPToolCallResult:
    content: list[dict[str, object]]
    structured_content: object
    is_error: bool


class OfficialMCPClient:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        http_client: httpx2.AsyncClient | None = None,
        pool_session: AbstractAsyncContextManager[httpx2.AsyncClient] | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._pool_session = pool_session

    async def list_tools(self) -> list[MCPDiscoveredTool]:
        async def operation() -> list[MCPDiscoveredTool]:
            async with self._open() as client:
                result = await client.list_tools()
                return [
                    MCPDiscoveredTool(
                        name=tool.name,
                        description=tool.description or tool.name,
                        input_schema=cast(dict[str, object], tool.input_schema),
                    )
                    for tool in result.tools
                ]

        return await self._with_timeout(operation())

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> MCPToolCallResult:
        async def operation() -> MCPToolCallResult:
            async with self._open() as client:
                result = await client.call_tool(name, dict(arguments))
                return MCPToolCallResult(
                    content=[
                        cast(
                            dict[str, object],
                            item.model_dump(mode="json", by_alias=True),
                        )
                        for item in result.content
                    ],
                    structured_content=result.structured_content,
                    is_error=result.is_error,
                )

        return await self._with_timeout(operation())

    async def _with_timeout[ResultT](self, awaitable: Awaitable[ResultT]) -> ResultT:
        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=self._config.timeout_ms / 1000,
            )
        except TimeoutError as exc:
            raise MCPTimeoutError(
                f"MCP {self._config.server_uri} exceeded {self._config.timeout_ms}ms"
            ) from exc
        except MCPRuntimeError:
            raise
        except Exception as exc:
            if _contains_timeout(exc):
                raise MCPTimeoutError(
                    f"MCP {self._config.server_uri} exceeded {self._config.timeout_ms}ms"
                ) from exc
            raise MCPTransportError(f"MCP {self._config.server_uri} failed: {exc}") from exc

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[Client]:
        timeout_seconds = self._config.timeout_ms / 1000
        if self._config.transport == "stdio":
            if self._config.command is None:
                raise MCPTransportError("stdio MCP command is required")
            params = StdioServerParameters(
                command=self._config.command,
                args=list(self._config.args),
                env=dict(self._config.env or {}),
                cwd=self._config.cwd,
            )
            async with Client(
                stdio_client(params),
                read_timeout_seconds=timeout_seconds,
            ) as client:
                yield client
            return
        if self._config.transport != "streamable_http" or self._config.url is None:
            raise MCPTransportError(f"unsupported MCP transport: {self._config.transport}")
        if self._pool_session is not None:
            # A18：pool session 在 enter 时 inc in_flight、exit 时 dec；MCP Client
            # session（含 handshake + call）整段被 in_flight 覆盖——evict/expire/
            # invalidate 三处 close 路径不会关掉本 client（含跨执行 version
            # invalidation race）。httpx2 transport client 复用 pool entry。
            async with (
                self._pool_session as http_client,
                self._open_http_session(http_client, timeout_seconds) as client,
            ):
                yield client
            return
        if self._http_client is not None:
            async with self._open_http_session(self._http_client, timeout_seconds) as client:
                yield client
            return
        timeout = httpx2.Timeout(timeout_seconds, read=timeout_seconds)
        limits = httpx2.Limits(max_connections=20, max_keepalive_connections=10)
        async with (
            httpx2.AsyncClient(
            headers=dict(self._config.headers or {}),
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            ) as http_client,
            self._open_http_session(http_client, timeout_seconds) as client,
        ):
            yield client

    @asynccontextmanager
    async def _open_http_session(
        self,
        http_client: httpx2.AsyncClient,
        timeout_seconds: float,
    ) -> AsyncIterator[Client]:
        assert self._config.url is not None
        transport = streamable_http_client(
            self._config.url,
            http_client=http_client,
            terminate_on_close=True,
        )
        async with Client(transport, read_timeout_seconds=timeout_seconds) as client:
            yield client


class RegistryMCPRuntime:
    def __init__(
        self,
        store: RegistryReadStore,
        *,
        credential_resolver: CredentialResolver | None = None,
        http_pool: MCPHTTPClientPool | None = None,
    ) -> None:
        self._store = store
        self._credential_resolver = credential_resolver
        self._http_pool = http_pool or MCPHTTPClientPool()

    async def close(self) -> None:
        await self._http_pool.close()

    async def prepare(
        self,
        context: RuntimeContext,
        tool_runtime: ToolRuntime,
    ) -> set[str]:
        tool_ids: set[str] = set()
        bindings = await self._mcp_bindings(context)
        for mcp_id, version in context.snapshot.mcp_versions.items():
            binding = bindings.get(mcp_id)
            if binding is None:
                continue
            config = await self._resolve_config(context, mcp_id, version, binding)
            client = await self._official_client(context, version, config)
            tools = await client.list_tools()
            for tool in tools:
                if config.allowed_tools and tool.name not in config.allowed_tools:
                    continue
                tool_id = mcp_tool_id(mcp_id, tool.name)
                tool_runtime.register(
                    ToolDescriptor(
                        tool_id=tool_id,
                        capability_id=f"mcp.{mcp_id}.{tool.name}",
                        name=tool.description,
                        parameters_schema=tool.input_schema,
                        external_dependency=True,
                        credential_ref=binding.credential_ref,
                    ),
                    self._executor(mcp_id, tool.name),
                )
                tool_ids.add(tool_id)
            context.emit(
                "mcp.tools_listed",
                {"mcp_id": mcp_id, "version": version, "tool_count": len(tool_ids)},
            )
        return tool_ids

    def _executor(self, mcp_id: str, tool_name: str) -> ToolExecutor:
        async def execute(
            context: RuntimeContext,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            return await self.call_tool(context, mcp_id, tool_name, arguments)

        return execute

    async def call_tool(
        self,
        context: RuntimeContext,
        mcp_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        version = context.snapshot.mcp_versions.get(mcp_id)
        if version is None:
            raise MCPToolCallError(f"MCP {mcp_id} is not in ExecutionSnapshot")
        binding = (await self._mcp_bindings(context)).get(mcp_id)
        if binding is None:
            raise MCPToolCallError(f"MCP {mcp_id} is not granted to user")
        config = await self._resolve_config(context, mcp_id, version, binding)
        if config.allowed_tools and tool_name not in config.allowed_tools:
            raise MCPToolCallError(f"MCP tool {mcp_id}/{tool_name} is not allowed")
        client = await self._official_client(context, version, config)
        result = await client.call_tool(tool_name, arguments)
        if result.is_error:
            raise MCPToolCallError(f"MCP tool {mcp_id}/{tool_name} returned an error")
        context.emit(
            "mcp.tool_called",
            {"mcp_id": mcp_id, "version": version, "tool_name": tool_name},
        )
        return {
            "content": result.content,
            "structured_content": result.structured_content,
        }

    async def _official_client(
        self,
        context: RuntimeContext,
        resource_version: str,
        config: MCPServerConfig,
    ) -> OfficialMCPClient:
        if config.transport != "streamable_http":
            return OfficialMCPClient(config)
        key = MCPHTTPPoolKey(
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            server_uri=config.server_uri,
            resource_version=resource_version,
            credential_version=config.credential_version,
        )
        pool_session = self._http_pool.session(
            key,
            headers=config.headers or {},
            timeout_ms=config.timeout_ms,
            credential_ref=config.credential_ref,
        )
        return OfficialMCPClient(config, pool_session=pool_session)

    async def _mcp_bindings(
        self,
        context: RuntimeContext,
    ) -> dict[str, ResourceBinding]:
        # A18/ADR-005：bindings 不随执行期变化，首次解析后缓存于 context，避免
        # 每次 call_tool/prepare 的 N+1 list_bindings 查询。
        if context.mcp_bindings_cache is not None:
            return context.mcp_bindings_cache
        bindings = await self._store.list_bindings(
            tenant_id=context.snapshot.tenant_id,
            subject_type="user",
            subject_id=context.snapshot.user_id,
            resource_type=ResourceKind.MCP,
        )
        result = {binding.resource_id: binding for binding in bindings if binding.enabled}
        context.mcp_bindings_cache = result
        return result

    async def _resolve_config(
        self,
        context: RuntimeContext,
        mcp_id: str,
        version: str,
        binding: ResourceBinding,
    ) -> MCPServerConfig:
        # A18/ADR-005：config（含已解算 credential）不随执行期变化，首次解析后
        # 缓存于 context，避免每次 call_tool 的 store.get + secret resolve。
        if context.mcp_configs_cache is not None:
            cached = context.mcp_configs_cache.get(mcp_id)
            if cached is not None:
                return cast(MCPServerConfig, cached)
        resource = await self._store.get(
            ResourceKind.MCP,
            mcp_id,
            tenant_id=context.snapshot.tenant_id,
            version=version,
        )
        if resource is None:
            raise MCPTransportError(f"MCP {mcp_id}@{version} not found")
        credential = await self._credential(binding)
        config = _server_config(resource, credential, binding.credential_ref)
        if context.mcp_configs_cache is None:
            context.mcp_configs_cache = {}
        context.mcp_configs_cache[mcp_id] = config
        return config

    async def _credential(self, binding: ResourceBinding) -> ResolvedCredential | None:
        if binding.credential_ref is None:
            return None
        if self._credential_resolver is None:
            raise MCPTransportError("MCP credential resolver is not configured")
        try:
            return await self._credential_resolver.resolve_with_metadata(
                binding.credential_ref, tenant_id=binding.tenant_id
            )
        except SecretProviderError:
            await self._http_pool.invalidate_credential(binding.credential_ref)
            raise


def mcp_tool_id(mcp_id: str, tool_name: str) -> str:
    return f"mcp__{_safe_tool_part(mcp_id)}__{_safe_tool_part(tool_name)}"


def _safe_tool_part(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def _server_config(
    resource: ResourceDefinition,
    credential: ResolvedCredential | None,
    credential_ref: str | None,
) -> MCPServerConfig:
    # ADR-012：从 MCPDefinition 实例取字段（单一真相源）；transport 必填、
    # stdio command / http url 必填、timeout 为正等约束由 model 校验保证。
    try:
        definition = MCPDefinition.model_validate(resource.spec_json)
    except ValidationError as exc:
        raise MCPTransportError(f"MCP {resource.id} spec invalid: {exc}") from exc
    allowed_tools = frozenset(definition.allowed_tools)
    if definition.transport == "stdio":
        env = dict(definition.env)
        if credential is not None and definition.credential_env is not None:
            env[definition.credential_env] = credential.value
        return MCPServerConfig(
            transport=definition.transport,
            server_uri=f"stdio://{resource.id}@{resource.version}",
            timeout_ms=definition.timeout_ms,
            command=definition.command or "",
            args=tuple(definition.args),
            env=env,
            cwd=Path(definition.cwd) if definition.cwd else None,
            allowed_tools=allowed_tools,
            credential_ref=credential_ref,
            credential_version=credential.version if credential is not None else "none",
        )
    if definition.transport == "streamable_http":
        headers = dict(definition.headers)
        if credential is not None:
            headers[definition.credential_header] = (
                f"{definition.credential_scheme} {credential.value}"
            )
        return MCPServerConfig(
            transport=definition.transport,
            server_uri=definition.url or "",
            timeout_ms=definition.timeout_ms,
            url=definition.url or "",
            headers=headers,
            allowed_tools=allowed_tools,
            credential_ref=credential_ref,
            credential_version=credential.version if credential is not None else "none",
        )
    raise MCPTransportError(f"unsupported MCP transport: {definition.transport}")


def _contains_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError | httpx2.TimeoutException):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains_timeout(nested) for nested in exc.exceptions)
    return False
