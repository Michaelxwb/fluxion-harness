from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from tests.channel_helpers import RecordingRuntime

from fluxion.plugins.channel_adapters import WebChannelAdapter
from fluxion.protocols.channel import ChannelResult, ExternalChannelMessage
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs, bind_codes
from fluxion.services.channel_app import ChannelApplicationService, ChannelBindError


@pytest.mark.asyncio
async def test_E_C109_expired_used_and_wrong_tenant_codes_are_rejected() -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    codes = iter(("EXPIRED-CODE", "USED-CODE", "TENANT-CODE"))
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ChannelApplicationService(
        store,
        RecordingRuntime(),
        code_factory=lambda: next(codes),
        clock=lambda: now,
    )
    await store.initialize()
    try:
        await service.create_platform_user("tenant-a", "user-a", display_name="用户 A")
        expired = await service.issue_bind_code(
            "tenant-a", "user-a", expires_at=now - timedelta(seconds=1)
        )
        used = await service.issue_bind_code("tenant-a", "user-a")
        tenant_bound = await service.issue_bind_code("tenant-a", "user-a")

        await _assert_bind_error(service, "tenant-a", "browser-expired", expired.code, "expired")
        first = await _bind(service, "tenant-a", "browser-used", used.code)
        assert first.kind == "bound"
        await _assert_bind_error(service, "tenant-a", "browser-other", used.code, "used")
        for attempt in range(5):
            await _assert_bind_error(
                service,
                "tenant-b",
                f"browser-b-{attempt}",
                tenant_bound.code,
                "tenant",
            )
        await _assert_bind_error(
            service, "tenant-a", "browser-frozen", tenant_bound.code, "frozen"
        )

        async with store.engine.connect() as connection:
            rows = (await connection.execute(select(bind_codes))).mappings().all()
            audits = (await connection.execute(select(audit_logs))).mappings().all()
        serialized = repr([dict(row) for row in (*rows, *audits)])
        assert expired.code not in serialized
        assert used.code not in serialized
        assert tenant_bound.code not in serialized
        rejected = [row for row in audits if row["action"] == "channel.bind.reject"]
        assert len(rejected) == 8
        assert {row["after_json"]["result"] for row in rejected} == {
            "expired",
            "frozen",
            "tenant",
            "used",
        }
    finally:
        await store.close()


async def _assert_bind_error(
    service: ChannelApplicationService,
    tenant_id: str,
    channel_user_id: str,
    code: str,
    expected_reason: str,
) -> None:
    with pytest.raises(ChannelBindError) as captured:
        await _bind(service, tenant_id, channel_user_id, code)
    assert captured.value.reason == expected_reason


async def _bind(
    service: ChannelApplicationService,
    tenant_id: str,
    channel_user_id: str,
    code: str,
) -> ChannelResult:
    return await service.handle(
        WebChannelAdapter(),
        ExternalChannelMessage(
            tenant_id=tenant_id,
            channel_user_id=channel_user_id,
            conversation_id=f"conversation-{channel_user_id}",
            message_id=f"message-{channel_user_id}",
            content=f"/bind {code}",
            runtime_profile_id="assistant",
        ),
    )
