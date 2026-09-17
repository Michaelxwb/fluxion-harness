from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from muad_api import AppError
from muad_contracts import (
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
)
from muad_im_gateway.application.console_client import ConsoleClient

RESOLVE_DATA = {
    "bound": True,
    "agent_id": str(uuid4()),
    "platform_user_id": str(uuid4()),
    "authorized": True,
}


def _resolve_request() -> ChannelResolveRequest:
    return ChannelResolveRequest(channel="WECOM", bot_id="bot-1", external_user_id="ext-1")


def _bind_request() -> ChannelBindRequest:
    return ChannelBindRequest(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="ext-1",
        bind_code="ABC123",
    )


async def test_resolve_posts_and_parses_response() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": "0", "msg": "成功", "data": RESOLVE_DATA})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        resolved = await client.resolve(_resolve_request(), "tenant-1")
    finally:
        await client.aclose()

    assert isinstance(resolved, ChannelResolveResponse)
    assert resolved.bound is True
    assert resolved.authorized is True
    assert captured[0].url.path == "/internal/channel/resolve"
    assert captured[0].headers["x-tenant-id"] == "tenant-1"
    assert captured[0].headers["x-caller-service"] == "muad-im-gateway"


async def test_bind_parses_response() -> None:
    platform_user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "成功",
                "data": {"platform_user_id": str(platform_user_id), "bound": True},
            },
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        bound = await client.bind(_bind_request(), "tenant-1")
    finally:
        await client.aclose()
    assert isinstance(bound, ChannelBindResponse)
    assert bound.platform_user_id == platform_user_id


async def test_bind_maps_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "BIND_CODE_INVALID", "msg": "invalid"})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.bind(_bind_request(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "BIND_CODE_INVALID"


async def test_bots_parses_snapshot() -> None:
    payload = {
        "code": "0",
        "msg": "成功",
        "data": {
            "revision": "sha256:abc",
            "items": [
                {
                    "bot_account_id": str(uuid4()),
                    "bot_id": "bot-1",
                    "secret_ref": "secret://wecom/bot-1",
                    "agent_id": str(uuid4()),
                    "enabled": True,
                }
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        snapshot = await client.bots("tenant-1")
    finally:
        await client.aclose()
    assert snapshot.revision == "sha256:abc"
    assert snapshot.items[0].bot_id == "bot-1"


async def test_channel_skills_returns_list() -> None:
    captured: list[httpx.Request] = []
    agent_id = uuid4()
    platform_user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"code": "0", "msg": "成功", "data": [{"name": "policy-check"}]},
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        skills = await client.channel_skills(agent_id, platform_user_id, "tenant-1")
    finally:
        await client.aclose()

    assert skills == [{"name": "policy-check"}]
    assert captured[0].url.path == "/internal/channel/skills"
    assert captured[0].url.params["agent_id"] == str(agent_id)
    assert captured[0].url.params["platform_user_id"] == str(platform_user_id)


async def test_channel_skills_accepts_items_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "0", "msg": "成功", "data": {"items": [{"name": "a"}, "skip-me"]}},
        )

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        skills = await client.channel_skills(uuid4(), uuid4(), "tenant-1")
    finally:
        await client.aclose()
    assert skills == [{"name": "a"}]


async def test_channel_skills_treats_404_as_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "COMMON_NOT_FOUND", "msg": "nope"})

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        assert await client.channel_skills(uuid4(), uuid4(), "tenant-1") == []
    finally:
        await client.aclose()


async def test_resolve_maps_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ConsoleClient("http://console.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AppError) as excinfo:
            await client.resolve(_resolve_request(), "tenant-1")
    finally:
        await client.aclose()
    assert excinfo.value.code == "COMMON_INTERNAL_ERROR"
