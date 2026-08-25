from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.console_helpers import create_resource, publish_resource

from fluxion.api.console import create_app
from fluxion.registry import (
    AuditRecord,
    BindingCommand,
    BindingOperation,
    RegistryStoreError,
    SQLiteRegistryStore,
)
from fluxion.registry.schema import audit_logs, outbox_events, publish_records, resource_bindings
from fluxion.resources import ResourceBinding, ResourceKind, ResourceStatus, SubjectType
from fluxion.services.console_app import ConsoleApplicationService


class AuditFailingStore(SQLiteRegistryStore):
    async def _insert_audit(
        self,
        connection: AsyncConnection,
        record: AuditRecord,
    ) -> None:
        del connection, record
        raise RegistryStoreError("audit store unavailable")


async def test_E_C112_audit_failure_rolls_back_high_impact_publish() -> None:
    store = AuditFailingStore("sqlite+aiosqlite:///:memory:")
    service = ConsoleApplicationService(store)
    await service.initialize()
    from httpx import ASGITransport, AsyncClient

    client = AsyncClient(
        transport=ASGITransport(app=create_app(service), raise_app_exceptions=False),
        base_url="http://console",
    )
    try:
        await create_resource(
            client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
        )
        response = await publish_resource(
            client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-E-C112",
        )
        resource = await store.get(
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            tenant_id="tenant-a",
            version="1",
        )
        async with store.engine.connect() as connection:
            counts = [
                (await connection.scalar(select(func.count()).select_from(table))) or 0
                for table in (publish_records, audit_logs, outbox_events)
            ]
        revision = await store.read_revision(tenant_id="tenant-a")
    finally:
        await client.aclose()
        await service.close()

    assert response.status_code == 500
    assert response.json()["code"] != 0
    assert resource is not None
    assert resource.status is ResourceStatus.DRAFT
    assert counts == [0, 0, 0]
    assert revision == 0


async def test_E_C112_binding_grant_rolls_back_when_audit_fails() -> None:
    """A12/A20：binding grant 审计失败 → commit_binding 整事务回滚（binding 行 +
    outbox + revision 均不落地）。此前 put_binding 先提交 binding、再跑独立
    _append_audit——失败时 binding 已落地，A20 的 fail-closed 对 binding 仅为
    装饰性。commit_binding 把 audit 收进同事务后，fail-closed 对 binding 真正
    生效（与 publish 治理 commit_publication 一致）。"""
    store = AuditFailingStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        command = BindingCommand(
            event_id="evt_E_C112_binding",
            tenant_id="tenant-a",
            binding_id="bind_E_C112",
            operation=BindingOperation.CREATE,
            actor_id="admin-a",
            request_id="req-E-C112-binding",
            trace_id="trace-E-C112",
            binding=ResourceBinding(
                binding_id="bind_E_C112",
                tenant_id="tenant-a",
                subject_type=SubjectType.USER,
                subject_id="user-a",
                resource_type=ResourceKind.MCP,
                resource_id="github",
                resource_version_selector="latest-published",
                config_json={"enabled_tools": ["list_pr"]},
                credential_ref=None,
                enabled=True,
            ),
        )
        with pytest.raises(RegistryStoreError, match="audit store unavailable"):
            await store.commit_binding(command)

        async with store.engine.connect() as connection:
            binding_count = await connection.scalar(
                select(func.count()).select_from(resource_bindings)
            )
            outbox_count = await connection.scalar(
                select(func.count()).select_from(outbox_events)
            )
            audit_count = await connection.scalar(
                select(func.count()).select_from(audit_logs)
            )
        revision = await store.read_revision(tenant_id="tenant-a")
    finally:
        await store.close()

    assert binding_count == 0
    assert outbox_count == 0
    assert audit_count == 0
    assert revision == 0
