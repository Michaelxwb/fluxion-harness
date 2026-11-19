"""Console bound IM targets client: only outbound channels, deduped, fail-closed."""

from __future__ import annotations

import uuid

import httpx
import pytest
from muad_agent_worker.infrastructure.channel_targets_client import (
    ConsoleChannelTargetsClient,
)

pytestmark = pytest.mark.unit

TENANT = "default"
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")
AGENT = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _client(handler) -> ConsoleChannelTargetsClient:
    return ConsoleChannelTargetsClient(
        "http://console.test",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service_token="service-token",
    )


async def test_targets_are_channel_neutral_and_deduped() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "items": [
                        {
                            "channel": "WECOM",
                            "bot_id": "bot-1",
                            "external_user_id": "user-1",
                            "external_conversation_id": None,
                            "agent_id": str(AGENT),
                            "bound_at": "2026-10-29T11:52:32.748206+00:00",
                        },
                        {
                            "channel": "WECOM",
                            "bot_id": "bot-1",
                            "external_user_id": "user-1",
                            "external_conversation_id": None,
                        },
                        {
                            "channel": "UNKNOWN",
                            "bot_id": "bot-2",
                            "external_user_id": "user-2",
                            "external_conversation_id": None,
                        },
                    ]
                },
            },
        )

    client = _client(handler)
    targets = await client.list_targets(
        tenant_id=TENANT, platform_user_id=USER, agent_id=AGENT
    )
    assert len(targets) == 1
    assert targets[0].channel == "WECOM"
    assert targets[0].bot_id == "bot-1"
    assert seen_request is not None
    assert seen_request.headers["X-Internal-Service"] == "service-token"
    assert seen_request.url.params["platform_user_id"] == str(USER)
    assert seen_request.url.params["agent_id"] == str(AGENT)


async def test_console_failure_degrades_to_no_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"code": "COMMON_INTERNAL_ERROR"})

    client = _client(handler)
    assert await client.list_targets(
        tenant_id=TENANT, platform_user_id=USER, agent_id=None
    ) == ()
