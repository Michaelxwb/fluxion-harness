from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from httpx import ASGITransport, AsyncClient, Response

from fluxion.api.console import create_app
from fluxion.registry import PostgreSQLRegistryStore, RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.secrets import LocalEncryptedSecretStore
from fluxion.services.console_app import ConsoleApplicationService
from tests.runtime_helpers import TEST_POSTGRES_DSN


@dataclass(slots=True)
class ConsoleTestStack:
    client: AsyncClient
    service: ConsoleApplicationService
    store: RegistryStore


@asynccontextmanager
async def console_stack(
    *,
    dsn: str | None = None,
    reset: bool = True,
) -> AsyncIterator[ConsoleTestStack]:
    """Console 测试栈（ADR-A007）：默认连本地 PG 并重建隔离。

    同一测试内多栈共享数据时（如 writer/reader），后开的栈传 `reset=False`。
    """
    store = PostgreSQLRegistryStore(dsn or TEST_POSTGRES_DSN, reset_on_initialize=reset)
    # TASK-009：注入可写 SecretStore（dev LocalEncryptedSecretStore），使
    # Credential 创建 Journey（明文只写）可测。
    secret_store = LocalEncryptedSecretStore(master_key=b"c" * 32)
    service = ConsoleApplicationService(store, secret_store=secret_store)
    await service.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://testserver",
    )
    try:
        yield ConsoleTestStack(client=client, service=service, store=store)
    finally:
        await client.aclose()
        await service.close()


def tenant_headers(
    tenant_id: str = "tenant-a",
    actor_id: str = "admin-a",
    request_id: str = "req-test",
    trace_id: str = "trace-test",
) -> dict[str, str]:
    return {
        "X-Tenant-ID": tenant_id,
        "X-Actor-ID": actor_id,
        "X-Request-ID": request_id,
        "X-Trace-ID": trace_id,
    }


async def create_resource(
    client: AsyncClient,
    *,
    kind: ResourceKind,
    resource_id: str,
    version: str = "1",
    tenant_id: str = "tenant-a",
    actor_id: str = "admin-a",
    visibility: str = "private",
    spec: Mapping[str, object] | None = None,
    request_id: str = "req-create",
) -> Response:
    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "resource_id": resource_id,
        "version": version,
        "visibility": visibility,
        "spec": dict(spec or runtime_profile_spec()),
    }
    return await client.post(
        f"/api/v1/resources/{kind.value}",
        json=payload,
        headers=tenant_headers(tenant_id, actor_id, request_id),
    )


async def publish_resource(
    client: AsyncClient,
    *,
    kind: ResourceKind,
    resource_id: str,
    version: str = "1",
    tenant_id: str = "tenant-a",
    actor_id: str = "admin-a",
    expected_base_version: str | None = "1",
    request_id: str = "req-publish",
) -> Response:
    payload: dict[str, object] = {"publish_note": "phase-06 acceptance"}
    if expected_base_version is not None:
        payload["expected_base_version"] = expected_base_version
    return await client.post(
        f"/api/v1/resources/{kind.value}/{resource_id}/versions/{version}:publish",
        json=payload,
        headers=tenant_headers(tenant_id, actor_id, request_id),
    )


async def rollback_resource(
    client: AsyncClient,
    *,
    kind: ResourceKind,
    resource_id: str,
    target_version: str,
    tenant_id: str = "tenant-a",
    actor_id: str = "admin-a",
    force: bool = False,
    approval_id: str | None = None,
    request_id: str = "req-rollback",
) -> Response:
    payload: dict[str, object] = {"target_version": target_version, "force": force}
    if approval_id is not None:
        payload["approval_id"] = approval_id
    return await client.post(
        f"/api/v1/resources/{kind.value}/{resource_id}:rollback",
        json=payload,
        headers=tenant_headers(tenant_id, actor_id, request_id),
    )


async def deprecate_resource(
    client: AsyncClient,
    *,
    kind: ResourceKind,
    resource_id: str,
    version: str,
    tenant_id: str = "tenant-a",
    actor_id: str = "admin-a",
    request_id: str = "req-deprecate",
) -> Response:
    return await client.post(
        f"/api/v1/resources/{kind.value}/{resource_id}/versions/{version}:deprecate",
        json={"reason": "acceptance test"},
        headers=tenant_headers(tenant_id, actor_id, request_id),
    )


def runtime_profile_spec() -> dict[str, object]:
    # V2（105 P1-01 方案 A）：仅有效字段；id/version/status 由资源外层
    # ResourceDefinition 承载，不进 spec。
    # ADR-A010（TASK-002）：fixture profile 作为租户默认（同名回退已废弃）。
    return {"max_rounds": 8, "default": True}


def mcp_spec(display_name: str = "github") -> dict[str, object]:
    # 与 MCPDefinition / runtime 契约一致：stdio 必须提供 command（server_uri
    # 由 runtime 自行构造，不读取 spec 字段）。
    return {
        "name": display_name,
        "display_name": display_name,
        "transport": "stdio",
        "command": "node",
        "args": ["github-mcp"],
        "env": {},
        "allowed_tools": ["list_pr", "get_repository"],
    }
