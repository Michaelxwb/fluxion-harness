from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.console_helpers import create_resource, publish_resource

from fluxion.api.console import create_app
from fluxion.registry import AuditRecord, RegistryStoreError, SQLiteRegistryStore
from fluxion.registry.schema import audit_logs, outbox_events, publish_records
from fluxion.resources import ResourceKind, ResourceStatus
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
