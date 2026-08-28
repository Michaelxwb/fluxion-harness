"""TASK-005（phase1-closure）S-06 路由级冒充收口验收测试。

S-06（E2E，RULE-fluxion-console-001 / RULE-C-04）：
- 已绑定用户 A 绑定后，攻击者以 A 的 channel_user_id 发送非 bind 消息（无凭据）
  → 必须被拒（401/403），不得映射 A 的 PlatformUser；
- 有效 Bearer Chat Access Token 的消息正常执行（per-message 验证）。

RED 语义：当前实现逐消息信任 payload.channel_user_id（S2 残留）——伪造请求会以
受害者身份执行并返回 200，断言 401 失败即证明偏差存在。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.registry import SQLiteRegistryStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.runtime_app import RuntimeApplicationService

BASE = "http://channel"


def _actor() -> ConsoleActor:
    return ConsoleActor(
        tenant_id="tenant-a",
        actor_id="admin-a",
        request_id="req-direct",
        trace_id="trace-direct",
    )


def _chat_payload(channel_user_id: str, content: str, message_id: str) -> dict[str, object]:
    return {
        "channel_user_id": channel_user_id,
        "conversation_id": f"conv-{channel_user_id}",
        "message_id": message_id,
        "content": content,
        "agent_id": "assistant",
    }


@pytest.mark.asyncio
async def test_s06_forged_bound_identity_rejected() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    channel = ChannelApplicationService(store, runtime)
    console = ConsoleApplicationService(store)
    app_fixture = _channel_app(channel)
    client = AsyncClient(transport=ASGITransport(app=app_fixture), base_url=BASE)
    try:
        await console.create_platform_user(
            _actor(), platform_user_id="user-victim", display_name="受害者"
        )
        await _publish_agent(store)
        code = await channel.issue_bind_code("tenant-a", "user-victim")
        bound = await client.post(
            "/api/v1/channels/web/messages",
            json=_chat_payload("victim-web-1", f"/bind {code.code}", "m-bind"),
            headers={"X-Tenant-ID": "tenant-a"},
        )
        assert bound.status_code == 200
        assert bound.json()["data"]["kind"] == "bound"

        # 冒充：攻击者以受害者 channel_user_id 发送非 bind 消息（无任何凭据）
        forged = await client.post(
            "/api/v1/channels/web/messages",
            json=_chat_payload("victim-web-1", "帮我查一下机密数据", "m-forge"),
            headers={"X-Tenant-ID": "tenant-a"},
        )
        assert forged.status_code in (401, 403), forged.text
    finally:
        await client.aclose()
        await runtime.close()
        await store.close()


@pytest.mark.asyncio
async def test_s06_valid_bearer_message_executes_as_token_user() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    channel = ChannelApplicationService(store, runtime)
    console = ConsoleApplicationService(store)
    app_fixture = _channel_app(channel)
    client = AsyncClient(transport=ASGITransport(app=app_fixture), base_url=BASE)
    try:
        await console.create_platform_user(
            _actor(), platform_user_id="user-token", display_name="持信者"
        )
        agent_id = await _publish_agent(store)
        issued = await console.issue_chat_access(
            _actor(), platform_user_id="user-token", agent_id=agent_id
        )
        response = await client.post(
            "/api/v1/channels/web/messages",
            json=_chat_payload("unbound-id-should-not-matter", "hello", "m-bearer"),
            headers={
                "Authorization": f"Bearer {issued.token}",
                "X-Tenant-ID": "tenant-a",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["kind"] == "message"
        assert body["platform_user_id"] == "user-token"
    finally:
        await client.aclose()
        await runtime.close()
        await store.close()


def _channel_app(channel: ChannelApplicationService):
    from fluxion.api.channel import create_app as create_channel_app

    return create_channel_app(channel)


async def _publish_agent(store: SQLiteRegistryStore) -> str:
    from tests.runtime_helpers import publish_resource, seed_agent_definition

    from fluxion.resources import ResourceKind

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。", provider_id="dev.echo")
    return "assistant"


# ---------------------------------------------------------------------------
# E-01（integration）：WeCom 签名 / Mattermost token 验证 + NFR-C-01 延迟
# ---------------------------------------------------------------------------


def _wecom_auth() -> WeComSignatureAuthenticator:
    import os

    from fluxion.services.channel_auth import WeComSignatureAuthenticator

    return WeComSignatureAuthenticator(secret=os.environ.get("WECOM_TEST_SECRET", "test-secret"))


def test_e01_wecom_valid_signature_passes() -> None:
    import hashlib
    import hmac

    auth = _wecom_auth()
    timestamp, nonce = "1710000000", "nonce-1"
    signature = hmac.new(
        b"test-secret", f"{timestamp}\n{nonce}".encode(), hashlib.sha256
    ).hexdigest()
    identity = auth.verify(timestamp=timestamp, nonce=nonce, signature=signature)
    assert identity.channel_type == "wecom"
    assert identity.verification_method == "wecom_signature"


def test_e01_wecom_invalid_signature_rejected() -> None:
    import pytest as _pytest

    from fluxion.services.channel_auth import ChannelAuthError

    auth = _wecom_auth()
    with _pytest.raises(ChannelAuthError) as error:
        auth.verify(timestamp="1710000000", nonce="nonce-1", signature="deadbeef")
    assert error.value.reason == "invalid_signature"
    assert error.value.method == "wecom_signature"


def test_e01_wecom_unconfigured_secret_fail_closed() -> None:
    import pytest as _pytest

    from fluxion.services.channel_auth import ChannelAuthError, WeComSignatureAuthenticator

    auth = WeComSignatureAuthenticator(secret="")
    with _pytest.raises(ChannelAuthError) as error:
        auth.verify(timestamp="t", nonce="n", signature="sig")
    assert error.value.reason == "secret_not_configured"


def test_e01_mattermost_token_verification() -> None:
    import pytest as _pytest

    from fluxion.services.channel_auth import (
        ChannelAuthError,
        MattermostTokenAuthenticator,
    )

    auth = MattermostTokenAuthenticator(expected_token="bot-token-123")
    identity = auth.verify("bot-token-123")
    assert identity.channel_type == "mattermost"
    with _pytest.raises(ChannelAuthError) as error:
        auth.verify("wrong-token")
    assert error.value.reason == "invalid_token"


def test_nfr_c01_verification_latency_p95_under_20ms() -> None:
    import hashlib
    import hmac
    import time

    auth = _wecom_auth()
    samples: list[float] = []
    for index in range(50):
        timestamp, nonce = str(1710000000 + index), f"nonce-{index}"
        signature = hmac.new(
            b"test-secret", f"{timestamp}\n{nonce}".encode(), hashlib.sha256
        ).hexdigest()
        start = time.perf_counter()
        auth.verify(timestamp=timestamp, nonce=nonce, signature=signature)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 <= 20, f"verification p95 {p95:.2f}ms exceeds 20ms budget"
