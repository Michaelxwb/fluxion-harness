"""[B-114] Runtime Egress 客户端 + 平台 Session 接入（HTTP resolve + PlatformAdapter + 真实 Redis）。

覆盖：四类 credential_mode、tenant/actor/version/adapter 隔离、adapter 变更旧 Session 失效。
"""

from __future__ import annotations

import uuid

import pytest
from muad_platform_sdk.egress_client import (
    EgressAccess,
    RuntimeEgressClient,
)
from muad_platform_sdk.session import RedisPlatformSessionInvalidator


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    """httpx.AsyncClient 兼容桩：记录请求并返回预设响应（仅传输层，不 mock 业务语义）。"""

    def __init__(self, responses: list[dict]) -> None:
        self.requests: list[dict] = []
        self._responses = list(responses)

    async def post(self, path: str, json: dict, headers: dict) -> _FakeResponse:
        self.requests.append({"path": path, "json": json, "headers": headers})
        return _FakeResponse(self._responses.pop(0) if self._responses else {"code": "0", "data": {}})

    async def aclose(self) -> None:
        pass


@pytest.fixture()
async def redis_client():
    import redis.asyncio as redis
    from muad_common import SharedSettings

    settings = SharedSettings()
    if not settings.redis_url:
        pytest.skip("REDIS_URL not configured")
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        keys = []
        async for key in client.scan_iter("muad:test-egress:*"):
            keys.append(key)
        if keys:
            await client.delete(*keys)
        await client.aclose()


def _egress_payload(platform_key: str, actor: str, mode: str = "USER_THEN_SHARED") -> dict:
    return {
        "actor_user_id": actor,
        "execution_ref": {"type": "RUN", "id": str(uuid.uuid4())},
        "platform_key": platform_key,
        "target": {"type": "PLATFORM_SERVICE", "service": "svc", "operation": "op"},
    }


def _egress_response(mode: str, credential_json: dict | None) -> dict:
    credential = (
        None
        if credential_json is None and mode == "NONE"
        else (
            {
                "ref_id": str(uuid.uuid4()),
                "credential_json": credential_json,
                "credential_schema_version": "1",
            }
            if credential_json is not None
            else None
        )
    )
    return {
        "code": "0",
        "data": {
            "decision": "ALLOW",
            "platform": {
                "id": str(uuid.uuid4()),
                "key": "p",
                "resolver_type": "BASE_URL",
                "resolver_config": {"base_url": "https://mssw.internal"},
                "adapter_key": "generic-http",
                "adapter_config": {},
                "adapter_schema_version": "1",
                "credential_mode": mode,
            },
            "credential": credential,
        },
    }


async def test_b114_credential_modes_resolution() -> None:
    """[B-114] 四类 credential_mode 均可解析且凭据进入 EgressAccess 内存对象。"""
    for mode, credential_json in (
        ("USER_ONLY", {"token": "u"}),
        ("SHARED_ONLY", {"token": "s"}),
        ("USER_THEN_SHARED", {"token": "us"}),
        ("NONE", None),
    ):
        transport = _RecordingClient([_egress_response(mode, credential_json)])
        client = RuntimeEgressClient(base_url="http://console", client=transport)  # type: ignore[arg-type]
        access = await client.resolve(
            tenant_id="t-1", payload=_egress_payload("p", "u-1")
        )
        await client.aclose()
        assert isinstance(access, EgressAccess)
        assert access.platform.credential_mode == mode
        if credential_json is None and mode == "NONE":
            assert access.credential is None
        else:
            assert access.credential is not None
            assert access.credential.credential_json == credential_json


async def test_b114_tenant_actor_isolation_in_request() -> None:
    """[B-114] tenant/actor 经 header/body 透传，不同租户请求互不可见。"""
    transport = _RecordingClient(
        [
            _egress_response("USER_ONLY", {"token": "a"}),
            _egress_response("USER_ONLY", {"token": "b"}),
        ]
    )
    client = RuntimeEgressClient(base_url="http://console", client=transport)  # type: ignore[arg-type]
    await client.resolve(tenant_id="tenant-a", payload=_egress_payload("p", "actor-a"))
    await client.resolve(tenant_id="tenant-b", payload=_egress_payload("p", "actor-b"))
    await client.aclose()

    tenants = {r["headers"]["X-Tenant-Id"] for r in transport.requests}
    assert tenants == {"tenant-a", "tenant-b"}
    actors = {r["json"]["actor_user_id"] for r in transport.requests}
    assert actors == {"actor-a", "actor-b"}


async def test_b114_session_invalidation_on_adapter_change(redis_client) -> None:
    """[B-114] adapter 变更：旧 Session 经 Redis Set 索引整体失效（clear_platform）。"""
    from muad_platform_sdk import platform_session_key, platform_sessions_index_key

    tenant_id = "t-1"
    platform_id = str(uuid.uuid4())
    actor_scope = "user:u-1"
    credential_version = "1"
    session_key = platform_session_key(
        tenant_id=tenant_id,
        platform_id=platform_id,
        actor_scope=actor_scope,
        credential_version=credential_version,
        adapter_key="generic-http",
        adapter_version="1",
    )
    index_key = platform_sessions_index_key(tenant_id=tenant_id, platform_id=platform_id)
    await redis_client.set(session_key, "v1")
    await redis_client.sadd(index_key, session_key)

    invalidator = RedisPlatformSessionInvalidator(redis_client)
    cleared = await invalidator.clear_platform(tenant_id=tenant_id, platform_id=platform_id)
    assert cleared == 1
    assert await redis_client.get(session_key) is None
    assert await redis_client.smembers(index_key) == set()
