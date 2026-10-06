from typing import Annotated

from fastapi import Depends, Request

from ..application.console_client import ConsoleClientPort
from ..channels.base import ChannelRegistry
from ..infrastructure.artifact_resolver import ArtifactResolverPort
from ..infrastructure.dedupe import DedupeStore


def get_registry(request: Request) -> ChannelRegistry:
    registry: ChannelRegistry = request.app.state.registry
    return registry


def get_dedupe_store(request: Request) -> DedupeStore:
    store: DedupeStore = request.app.state.dedupe_store
    return store


def get_console_client(request: Request) -> ConsoleClientPort:
    client: ConsoleClientPort = request.app.state.console_client
    return client


def get_gateway_tenant(request: Request) -> str:
    """**本网关部署的租户**（单租户部署）：投递请求声明的租户必须与它一致。

    与入站用的 `DEFAULT_TENANT_ID` 是同一个值 —— 一个网关实例只服务一个租户的 bot，
    所以"另一个租户的投递"在这里没有任何合法含义。
    """
    tenant_id: str = request.app.state.tenant_id
    return tenant_id


def get_artifact_resolver(request: Request) -> ArtifactResolverPort:
    resolver: ArtifactResolverPort = request.app.state.artifact_resolver
    return resolver


ConsoleClientDep = Annotated[ConsoleClientPort, Depends(get_console_client)]
RegistryDep = Annotated[ChannelRegistry, Depends(get_registry)]
DedupeStoreDep = Annotated[DedupeStore, Depends(get_dedupe_store)]
GatewayTenantDep = Annotated[str, Depends(get_gateway_tenant)]
ArtifactResolverDep = Annotated[ArtifactResolverPort, Depends(get_artifact_resolver)]
