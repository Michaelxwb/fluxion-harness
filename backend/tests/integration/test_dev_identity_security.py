from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tests.channel_helpers import RecordingRuntime

from fluxion.api import console_errors
from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.config import DevModeSettings
from fluxion.resources import ResourceKind
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs, chat_access_tokens
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService


@pytest.mark.asyncio
async def test_E_P13_02_forged_headers_tampered_and_revoked_tokens_fail_closed() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime = RecordingRuntime()
    settings = DevModeSettings(enabled=True)
    console = ConsoleApplicationService(store)
    channel = ChannelApplicationService(store, runtime)
    await store.initialize()
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=create_console_app(console, dev_mode=settings)),
                base_url="http://console",
            ) as console_client,
            AsyncClient(
                transport=ASGITransport(app=create_channel_app(channel, dev_mode=settings)),
                base_url="http://chat",
            ) as chat_client,
        ):
            forged = {"X-Tenant-ID": "tenant-evil", "X-Actor-ID": "actor-evil"}
            await console_client.post(
                "/api/v1/platform-users",
                json={"platform_user_id": "user-a", "display_name": "用户 A"},
                headers=forged,
            )
            from tests.runtime_helpers import publish_resource, seed_agent_definition as _sd
            await publish_resource(store, tenant_id="dev", kind=ResourceKind.RUNTIME_PROFILE,
                                   resource_id="assistant", version="1",
                                   spec={"request_timeout_ms":30_000,"max_retries":1})
            await _sd(store, tenant_id="dev", provider_id="dev.echo")
            issued = await console_client.post(
                "/api/v1/platform-users/user-a/chat-access",
                json={"agent_id": "assistant"},
                headers=forged,
            )
            access = issued.json()["data"]
            token = access["token"]

            tampered = await chat_client.post(
                "/api/v1/channels/web/access/messages",
                json=_message_payload("tampered"),
                headers={"Authorization": f"Bearer {token}x"},
            )
            revoked = await console_client.post(
                f"/api/v1/chat-access/{access['access_id']}:revoke",
                headers=forged,
            )
            after_revoke = await chat_client.post(
                "/api/v1/channels/web/access/messages",
                json=_message_payload("revoked"),
                headers={"Authorization": f"Bearer {token}"},
            )

        assert issued.status_code == 200
        assert tampered.status_code == 401
        assert tampered.json()["code"] == 36_003
        assert revoked.status_code == 200
        assert after_revoke.status_code == 401
        assert len(runtime.requests) == 0

        async with store.engine.connect() as connection:
            access_row = (await connection.execute(select(chat_access_tokens))).mappings().one()
            audits = (await connection.execute(select(audit_logs))).mappings().all()
        assert access_row["revoked_at"] is not None
        assert token not in repr(dict(access_row))
        relevant = [row for row in audits if str(row["action"]).startswith("chat_access.")]
        assert {row["actor_id"] for row in relevant} == {"admin"}
        assert {row["tenant_id"] for row in relevant} == {"dev"}
        assert token not in repr([dict(row) for row in relevant])
    finally:
        await store.close()


def _message_payload(content: str) -> dict[str, str]:
    return {
        "conversation_id": "conversation-a",
        "message_id": f"message-{content}",
        "content": content,
    }


@pytest.mark.asyncio
async def test_E_P13_02_error_log_uses_trusted_dev_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ConsoleApplicationService(store)
    emitted: list[dict[str, object]] = []

    async def fail_list(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("forced failure")

    monkeypatch.setattr(service, "list_platform_users", fail_list)
    monkeypatch.setattr(console_errors, "emit_error_log", lambda **values: emitted.append(values))
    await store.initialize()
    try:
        async with AsyncClient(
            transport=ASGITransport(
                app=create_console_app(service, dev_mode=DevModeSettings(enabled=True)),
                raise_app_exceptions=False,
            ),
            base_url="http://console",
        ) as client:
            response = await client.get(
                "/api/v1/platform-users",
                headers={"X-Tenant-ID": "tenant-evil", "X-Actor-ID": "actor-evil"},
            )

        assert response.status_code == 500
        assert emitted[0]["tenant_id"] == "dev"
        assert emitted[0]["actor_id"] == "admin"
    finally:
        await store.close()
