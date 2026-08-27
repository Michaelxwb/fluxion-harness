from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from httpx import ASGITransport, AsyncClient, Response

from fluxion.api.console import create_app
from fluxion.registry import RegistryStore, SQLiteRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService


@dataclass(slots=True)
class ConsoleTestStack:
    client: AsyncClient
    service: ConsoleApplicationService
    store: RegistryStore


@asynccontextmanager
async def console_stack(
    *,
    dsn: str | None = None,
    db_path: Path | None = None,
) -> AsyncIterator[ConsoleTestStack]:
    database_dsn = dsn or (
        "sqlite+aiosqlite:///:memory:"
        if db_path is None
        else f"sqlite+aiosqlite:///{db_path}"
    )
    store = SQLiteRegistryStore(database_dsn)
    service = ConsoleApplicationService(store)
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
    # ADR-012 / TASK-A104：与收缩后的 RuntimeProfile 字段集一致（纯 mechanics；
    # persona/model/capability 在 AgentDefinition）。id/version/status 由资源外层
    # ResourceDefinition 承载，不进 spec。
    return {"request_timeout_ms": 30_000, "max_retries": 1}


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
