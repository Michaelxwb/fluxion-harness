from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import httpx
import uvicorn
from httpx import ASGITransport, AsyncClient
from muad_im_gateway.api.deps import get_dedupe_store, get_registry
from muad_contracts import BotSnapshotItem
from muad_im_gateway.channels.base import ChannelAdapterUnavailable, ChannelRegistry
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    DedupeStore,
    DedupeStoreError,
    InMemoryDedupeStore,
    NullDedupeStore,
)
from muad_im_gateway.main import app

from tests.e2e.wecom_probe_app import WeComProbe


class _FailingAdapter(FakeChannelAdapter):
    async def send(self, route, message) -> None:  # type: ignore[no-untyped-def]
        raise ChannelAdapterUnavailable("sdk missing")


class _FailingDedupeStore:
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        raise DedupeStoreError("redis down")

    async def exists(self, key: str) -> bool:
        raise DedupeStoreError("redis down")

    async def mark(self, key: str, ttl_sec: int) -> None:
        raise DedupeStoreError("redis down")

    async def reserve(self, key: str, ttl_sec: int) -> bool:
        raise DedupeStoreError("redis down")

    async def get_value(self, key: str) -> str | None:
        raise DedupeStoreError("redis down")

    async def release(self, key: str) -> None:
        raise DedupeStoreError("redis down")

    async def aclose(self) -> None:
        return None


def delivery_body(
    delivery_key: str | None = None, task_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """构造投递请求体；task_id 与 delivery_key 必须指向同一 Task（契约要求）。"""
    resolved_task_id = task_id or uuid.uuid4()
    return {
        "task_id": str(resolved_task_id),
        "delivery_key": delivery_key or f"task:{resolved_task_id}:final",
        "route": {
            "channel": "WECOM",
            "bot_id": "bot-1",
            "external_user_id": "ext-1",
            "external_conversation_id": "conv-1",
        },
        "message": {"type": "text", "text": "任务已完成：成功 18，失败 2。"},
        "artifact_ids": [],
    }


@asynccontextmanager
async def api_client(
    registry: ChannelRegistry,
    dedupe: DedupeStore,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: dedupe
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _registry_with(adapter: FakeChannelAdapter) -> ChannelRegistry:
    registry = ChannelRegistry()
    registry.register(adapter)
    return registry


async def test_first_delivery_is_accepted_and_sent() -> None:
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert body["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    route, message = adapter.sent[0]
    assert route.bot_id == "bot-1"
    assert message.text == "任务已完成：成功 18，失败 2。"


async def test_duplicate_delivery_is_acknowledged_without_resending() -> None:
    adapter = FakeChannelAdapter()
    body = delivery_body()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        first = await client.post("/internal/deliveries", json=body)
        second = await client.post("/internal/deliveries", json=body)

    assert first.status_code == 200
    assert first.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert second.status_code == 200
    assert second.json()["data"] == {
        "accepted": True,
        "duplicate": True,
        "delivered": True,
        "deduplicated": True,
    }
    assert len(adapter.sent) == 1


async def test_null_dedupe_store_sends_every_delivery() -> None:
    adapter = FakeChannelAdapter()
    body = delivery_body()
    async with api_client(_registry_with(adapter), NullDedupeStore()) as client:
        first = await client.post("/internal/deliveries", json=body)
        second = await client.post("/internal/deliveries", json=body)

    assert first.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert second.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert len(adapter.sent) == 2


async def test_adapter_failure_returns_internal_error_and_keeps_retry_available() -> None:
    failing = _FailingAdapter()
    store = InMemoryDedupeStore()
    body = delivery_body()
    async with api_client(_registry_with(failing), store) as client:
        response = await client.post("/internal/deliveries", json=body)

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert await store.exists("delivery:dedupe:" + body["delivery_key"]) is False

    working = FakeChannelAdapter()
    async with api_client(_registry_with(working), store) as client:
        retry = await client.post("/internal/deliveries", json=body)

    assert retry.status_code == 200
    assert retry.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert len(working.sent) == 1


async def test_unknown_adapter_returns_internal_error() -> None:
    async with api_client(ChannelRegistry(), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"


async def test_dedupe_failure_degrades_to_at_least_once_send() -> None:
    """RULE-13：运行期 Redis 故障不停发，降级为 at-least-once 并如实标注未去重。"""
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), _FailingDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 200
    assert response.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert len(adapter.sent) == 1


async def test_dedupe_failure_with_send_failure_is_not_faked_as_delivered() -> None:
    adapter = _FailingAdapter()
    async with api_client(_registry_with(adapter), _FailingDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body())

    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"


async def test_invalid_delivery_key_is_rejected() -> None:
    adapter = FakeChannelAdapter()
    async with api_client(_registry_with(adapter), InMemoryDedupeStore()) as client:
        response = await client.post("/internal/deliveries", json=delivery_body("not-a-key"))

    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


class _HttpProbe:
    """本地真实 HTTP 探针（真实 socket），记录渠道侧收到的投递。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._server: Any = None
        self._thread: Any = None
        self.url = ""

    def start(self) -> None:
        import http.server
        import threading

        probe = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                probe.requests.append(payload)
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"accepted": true}')

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return None

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/probe"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)


class _ProbeAdapter(FakeChannelAdapter):
    def __init__(self, url: str, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        super().__init__()
        self._url = url
        self._fail = fail
        self._gate = gate

    async def send(self, route, message) -> None:  # type: ignore[no-untyped-def]
        if self._fail:
            raise ChannelAdapterUnavailable("probe rejected")
        if self._gate is not None:
            await self._gate.wait()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._url,
                json={"text": message.text, "bot_id": route.bot_id},
            )
            response.raise_for_status()


async def _redis_store() -> Any:
    import redis.asyncio
    from muad_im_gateway.infrastructure.dedupe import RedisDedupeStore
    from muad_common import SharedSettings

    url = SharedSettings().redis_url
    if not url:
        pytest.skip("REDIS_URL not configured")
    client = redis.asyncio.Redis.from_url(url)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - 环境缺失时跳过
        await client.aclose()
        pytest.skip(f"redis unavailable: {exc}")
    return client, RedisDedupeStore(client)


async def test_b121_atomic_dedupe_sends_once_under_concurrency() -> None:
    """并发同 delivery_key：SET NX 原子占位，渠道只被真实调用一次。"""
    probe = _HttpProbe()
    probe.start()
    client, store = await _redis_store()
    body = delivery_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    gate = asyncio.Event()
    adapter = _ProbeAdapter(probe.url, gate=gate)
    try:
        async with api_client(_registry_with(adapter), store) as api:
            first = asyncio.create_task(api.post("/internal/deliveries", json=body))
            await asyncio.sleep(0.1)
            try:
                second = await asyncio.wait_for(
                    api.post("/internal/deliveries", json=body), timeout=3
                )
            except TimeoutError as exc:
                gate.set()
                await first
                raise AssertionError("并发第二个请求被发送阻塞：缺少原子占位") from exc
            gate.set()
            first_response = await first
        assert first_response.status_code == 200
        assert first_response.json()["data"] == {
            "accepted": True,
            "duplicate": False,
            "delivered": True,
            "deduplicated": False,
        }
        # 占位中的重复：有界等待成功键后回可重试错误，绝不把占位当成功
        assert second.status_code >= 400
        assert second.json()["code"] == "COMMON_INTERNAL_ERROR"
        assert len(probe.requests) == 1
        ttl = await client.ttl(key)
        assert ttl > 6 * 24 * 3600, f"送达标记必须保留 7d，实际 {ttl}s"
    finally:
        await client.delete(key)
        await client.aclose()
        probe.stop()


async def test_b121_duplicate_after_delivery_is_acknowledged_as_delivered() -> None:
    probe = _HttpProbe()
    probe.start()
    client, store = await _redis_store()
    body = delivery_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    adapter = _ProbeAdapter(probe.url)
    try:
        async with api_client(_registry_with(adapter), store) as api:
            first = await api.post("/internal/deliveries", json=body)
            second = await api.post("/internal/deliveries", json=body)
        assert first.json()["data"] == {
            "accepted": True,
            "duplicate": False,
            "delivered": True,
            "deduplicated": False,
        }
        assert second.json()["data"] == {
            "accepted": True,
            "duplicate": True,
            "delivered": True,
            "deduplicated": True,
        }
        assert len(probe.requests) == 1
    finally:
        await client.delete(key)
        await client.aclose()
        probe.stop()


async def test_b121_failed_send_releases_placeholder_for_retry() -> None:
    probe = _HttpProbe()
    probe.start()
    client, store = await _redis_store()
    body = delivery_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    try:
        async with api_client(_registry_with(_ProbeAdapter(probe.url, fail=True)), store) as api:
            failed = await api.post("/internal/deliveries", json=body)
        assert failed.status_code == 500
        assert await client.exists(key) == 0, "发送失败必须释放占位，允许重试"

        async with api_client(_registry_with(_ProbeAdapter(probe.url)), store) as api:
            retried = await api.post("/internal/deliveries", json=body)
        assert retried.status_code == 200
        assert retried.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
        assert len(probe.requests) == 1
    finally:
        await client.delete(key)
        await client.aclose()
        probe.stop()


async def test_b121_crash_window_releases_placeholder_without_faking_delivery() -> None:
    """占位后崩溃：占位带短 TTL，重试不会把未送达当成已送达，过期后真实补发。"""
    probe = _HttpProbe()
    probe.start()
    client, store = await _redis_store()
    body = delivery_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    await client.set(key, "in-flight", ex=1)
    adapter = _ProbeAdapter(probe.url)
    try:
        async with api_client(_registry_with(adapter), store) as api:
            during = await api.post("/internal/deliveries", json=body)
            # 只有占位、无成功键：有界等待后回可重试错误，不冒充已送达
            assert during.status_code >= 400
            assert during.json()["code"] == "COMMON_INTERNAL_ERROR"
            assert len(probe.requests) == 0
            await asyncio.sleep(1.2)
            after = await api.post("/internal/deliveries", json=body)
        assert after.json()["data"] == {
            "accepted": True,
            "duplicate": False,
            "delivered": True,
            "deduplicated": False,
        }
        assert len(probe.requests) == 1
    finally:
        await client.delete(key)
        await client.aclose()
        probe.stop()


async def test_b121_redis_unavailable_degrades_to_at_least_once() -> None:
    """Redis 不可用：不宣称 exactly-once，可能重复但每次都是真实发送。"""
    probe = _HttpProbe()
    probe.start()
    body = delivery_body()
    adapter = _ProbeAdapter(probe.url)
    async with api_client(_registry_with(adapter), NullDedupeStore()) as api:
        first = await api.post("/internal/deliveries", json=body)
        second = await api.post("/internal/deliveries", json=body)
    assert first.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert second.json()["data"] == {
        "accepted": True,
        "duplicate": False,
        "delivered": True,
        "deduplicated": False,
    }
    assert len(probe.requests) == 2
    probe.stop()


async def test_e05_worker_http_to_gateway_redis_probe_end_to_end() -> None:
    """E-05：Worker HTTP → 真实 Gateway（ASGI）→ 真实 Redis → 本地渠道探针。"""
    from muad_agent_worker.application.delivery_routes import upsert_delivery_route
    from muad_agent_worker.delivery.client import HttpDeliveryClient
    from muad_agent_worker.delivery.service import DeliveryLoop
    from muad_agent_worker.infrastructure.db import get_session_factory
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from muad_common import SharedSettings
    from muad_contracts import DeliveryRouteInput
    from sqlalchemy import text

    settings = SharedSettings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    probe = _HttpProbe()
    probe.start()
    client, store = await _redis_store()
    adapter = _ProbeAdapter(probe.url)
    session_factory = get_session_factory()
    tenant_id = f"test-{uuid.uuid4()}"
    task_id = uuid.uuid4()
    now = datetime.now(UTC)
    key = f"delivery:dedupe:task:{task_id}:final"
    try:
        async with session_factory() as session:
            async with session.begin():
                route_id = await upsert_delivery_route(
                    session,
                    tenant_id=tenant_id,
                    platform_user_id=uuid.uuid4(),
                    route=DeliveryRouteInput(
                        channel="WECOM", bot_id="bot-e05", external_user_id="ext-e05"
                    ),
                )
                session.add(
                    TaskExecution(
                        id=task_id,
                        tenant_id=tenant_id,
                        agent_id=uuid.uuid4(),
                        actor_user_id=uuid.uuid4(),
                        intent_key="policy_check",
                        skill_id=uuid.uuid4(),
                        skill_artifact_id=uuid.uuid4(),
                        trigger_type="IMMEDIATE",
                        execution_mode="ASYNC",
                        task_type="SKILL",
                        status="COMPLETED",
                        input_json={},
                        execution_snapshot_schema_version=1,
                        execution_snapshot_json={"schema_version": 1},
                        snapshot_hash="sha256:" + "a" * 64,
                        idempotency_key=f"e05-{task_id}",
                        priority=100,
                        attempt=0,
                        max_attempts=3,
                        not_before=now,
                        deadline_at=now + timedelta(hours=1),
                        delivery_route_id=route_id,
                        delivery_mode="FINAL_ONLY",
                        delivery_status="PENDING",
                        delivery_key=f"task:{task_id}:final",
                        delivery_attempts=0,
                        finished_at=now,
                    )
                )
        body = delivery_body(task_id=task_id)
        async with api_client(_registry_with(adapter), store) as gateway_http:
            worker = DeliveryLoop(
                session_factory,
                HttpDeliveryClient("http://gateway", gateway_http),
                settings,
            )
            first = await worker.run_once()
            assert first is not None and first.sent is True
            assert len(probe.requests) == 1

            replay = await gateway_http.post("/internal/deliveries", json=body)
            assert replay.status_code == 200
            assert replay.json()["data"] == {
                "accepted": True,
                "duplicate": True,
                "delivered": True,
                "deduplicated": True,
            }
            assert len(probe.requests) == 1, "Redis 正常时重放不得重复发送"

        async with session_factory() as session:
            row = await session.get(TaskExecution, task_id)
            assert row is not None and row.delivery_status == "SENT"
    finally:
        await client.delete(key)
        await client.aclose()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM task.task_event WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await session.execute(
                text("DELETE FROM task.task_execution WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await session.execute(
                text("DELETE FROM task.delivery_route WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await session.commit()
        probe.stop()


# ---------------------------------------------------------------------------
# B-117: 主动投递响应与渠道错误（真实 Gateway HTTP→生产 WeComAdapter→真实 Redis/本地 WS）
# ---------------------------------------------------------------------------

B117_BOT_A = "bot-b117-a"
B117_BOT_B = "bot-b117-b"
B117_SECRET = "b117-secret"
B117_EXTERNAL_USER = "ext-b117"
B117_CHAT = "chat-b117"
DELIVERY_TTL_SEC = 604800
DELIVERY_IN_FLIGHT_WAIT_SEC = 2.0  # 与生产常量一致（有界等待成功键）


def _b117_bot(bot_id: str) -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid.uuid4(), bot_id=bot_id, secret=B117_SECRET, agent_id=uuid.uuid4()
    )


def _b117_body(*, bot_id: str = B117_BOT_A, delivery_key: str | None = None) -> dict[str, Any]:
    key = delivery_key or f"task:{uuid.uuid4()}:final"
    return {
        "task_id": key.split(":")[1],
        "delivery_key": key,
        "route": {
            "channel": "WECOM",
            "bot_id": bot_id,
            "external_user_id": B117_EXTERNAL_USER,
            "external_conversation_id": B117_CHAT,
        },
        "message": {"type": "text", "text": "任务已完成：成功 18，失败 2。"},
        "artifact_ids": [str(uuid.uuid4())],  # 大结果引用：不得转成 IM 下载入口
    }


@asynccontextmanager
async def _b117_gateway_http(registry: ChannelRegistry, dedupe: Any) -> AsyncIterator[AsyncClient]:
    """真实 Gateway HTTP（uvicorn + 真实 socket；lifespan 关闭只保留依赖注入）。"""
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: dedupe
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 15.0
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "gateway 未在超时内监听"
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=15.0)
        app.dependency_overrides.clear()


@asynccontextmanager
async def _b117_wecom_stack(
    bots: list[str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[ChannelRegistry, WeComAdapter, Any]]:
    """生产 WeComAdapter → 官方 SDK → 真实本地 WS 探针（真实 TLS socket）。"""
    probe = WeComProbe(expected_bots={bot_id: B117_SECRET for bot_id in bots})
    await probe.start()
    monkeypatch.setenv("WECOM_WS_URL", probe.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(probe.cert_path))
    adapter = WeComAdapter(
        bots=[_b117_bot(bot_id) for bot_id in bots],
        backoff_base_sec=0.2,
        backoff_max_sec=0.5,
        liveness_interval_sec=0.2,
    )
    registry = ChannelRegistry()
    registry.register(adapter)
    await registry.start_all()
    try:
        yield registry, adapter, probe
    finally:
        await registry.stop_all()
        await probe.stop()


async def _b117_wait_connected(adapter: WeComAdapter, bot_id: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if adapter.connection_states.get(bot_id) is ConnectionState.CONNECTED:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{bot_id} 未在真实 WS 上完成认证")


def _b117_sent_frames(probe: Any) -> dict[str, str]:
    """探针回读：bot_id（按连接归属）→ 出站文本。"""
    return {
        probe.connection_bots[item.connection]: item.frame["body"]["text"]["content"]
        for item in probe.received
        if item.frame.get("cmd") == "aibot_send_msg"
    }


async def test_b117_replay_is_deduplicated_with_7d_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client, store = await _redis_store()
    body = _b117_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    try:
        async with _b117_wecom_stack([B117_BOT_A], monkeypatch) as (registry, adapter, probe):
            await _b117_wait_connected(adapter, B117_BOT_A)
            async with _b117_gateway_http(registry, store) as http:
                first = await http.post("/internal/deliveries", json=body)
                assert first.status_code == 200, first.text
                assert first.json()["data"] == {
                    "accepted": True,
                    "duplicate": False,
                    "delivered": True,
                    "deduplicated": False,
                }
                frames = await probe.wait_for_replies(1)
                sent_text = frames[-1]["body"]["text"]["content"]
                assert sent_text == body["message"]["text"]  # 不追加下载入口/链接
                assert body["artifact_ids"][0] not in sent_text

                replay = await http.post("/internal/deliveries", json=body)
                assert replay.status_code == 200, replay.text
                assert replay.json()["data"] == {
                    "accepted": True,
                    "duplicate": True,
                    "delivered": True,
                    "deduplicated": True,
                }
                await asyncio.sleep(0.3)
                assert len(probe.replies) == 1, "重放不得再次发送"

                ttl = await redis_client.ttl(key)
                assert DELIVERY_TTL_SEC - 60 < ttl <= DELIVERY_TTL_SEC
    finally:
        await redis_client.delete(key)
        await redis_client.aclose()


async def test_b117_delivery_uses_bot_from_route(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client, store = await _redis_store()
    body_a = _b117_body(bot_id=B117_BOT_A)
    body_b = _b117_body(bot_id=B117_BOT_B)
    keys = ["delivery:dedupe:" + body_a["delivery_key"], "delivery:dedupe:" + body_b["delivery_key"]]
    try:
        async with _b117_wecom_stack([B117_BOT_A, B117_BOT_B], monkeypatch) as (
            registry,
            adapter,
            probe,
        ):
            await _b117_wait_connected(adapter, B117_BOT_A)
            await _b117_wait_connected(adapter, B117_BOT_B)
            async with _b117_gateway_http(registry, store) as http:
                for body in (body_a, body_b):
                    response = await http.post("/internal/deliveries", json=body)
                    assert response.status_code == 200, response.text
                await probe.wait_for_replies(2)
                sent = _b117_sent_frames(probe)
                assert sent == {
                    B117_BOT_A: body_a["message"]["text"],
                    B117_BOT_B: body_b["message"]["text"],
                }
    finally:
        for key in keys:
            await redis_client.delete(key)
        await redis_client.aclose()


async def test_b117_unknown_bot_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client, store = await _redis_store()
    body = _b117_body(bot_id="bot-b117-missing")
    try:
        async with _b117_wecom_stack([B117_BOT_A], monkeypatch) as (registry, _adapter, probe):
            async with _b117_gateway_http(registry, store) as http:
                response = await http.post("/internal/deliveries", json=body)
            assert response.status_code == 404, response.text
            assert response.json()["code"] == "BOT_NOT_FOUND"
            assert probe.replies == []
    finally:
        await redis_client.delete("delivery:dedupe:" + body["delivery_key"])
        await redis_client.aclose()


async def test_b117_send_failure_is_not_accepted_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client, store = await _redis_store()
    body = _b117_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    try:
        async with _b117_wecom_stack([B117_BOT_A], monkeypatch) as (registry, adapter, probe):
            await _b117_wait_connected(adapter, B117_BOT_A)
            probe.fail_reply_bots.add(B117_BOT_A)  # 故障注入：SDK 发送失败
            async with _b117_gateway_http(registry, store) as http:
                failed = await http.post("/internal/deliveries", json=body)
                assert failed.status_code >= 400, failed.text
                assert failed.json()["code"] == "COMMON_INTERNAL_ERROR"
                assert (failed.json().get("data") or {}).get("accepted") is not True

                probe.clear_injections()
                retried = await http.post("/internal/deliveries", json=body)
                assert retried.status_code == 200, retried.text
                assert retried.json()["data"]["delivered"] is True
                assert retried.json()["data"]["deduplicated"] is False
    finally:
        await redis_client.delete(key)
        await redis_client.aclose()


async def test_b117_in_flight_duplicate_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client, store = await _redis_store()
    body = _b117_body()
    key = "delivery:dedupe:" + body["delivery_key"]
    try:
        async with _b117_wecom_stack([B117_BOT_A], monkeypatch) as (registry, adapter, probe):
            await _b117_wait_connected(adapter, B117_BOT_A)
            # 只占位、未送达（模拟另一实例正在发送或上一轮崩溃窗口）
            assert await store.reserve(key, int(DELIVERY_IN_FLIGHT_WAIT_SEC * 5)) is True
            async with _b117_gateway_http(registry, store) as http:
                started = time.monotonic()
                pending = await http.post("/internal/deliveries", json=body)
                elapsed = time.monotonic() - started
                assert pending.status_code >= 400, pending.text
                assert pending.json()["code"] == "COMMON_INTERNAL_ERROR"
                assert elapsed >= DELIVERY_IN_FLIGHT_WAIT_SEC  # 有界等待后才报可重试错误
                assert probe.replies == [], "占位中的重复不得触发发送"

                # 成功键落库后，同一 key 的重放按去重成功返回
                await store.mark(key, DELIVERY_TTL_SEC)
                replayed = await http.post("/internal/deliveries", json=body)
                assert replayed.status_code == 200, replayed.text
                assert replayed.json()["data"]["deduplicated"] is True
                assert probe.replies == []
    finally:
        await redis_client.delete(key)
        await redis_client.aclose()
