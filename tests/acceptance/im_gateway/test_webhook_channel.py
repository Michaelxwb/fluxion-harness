"""S-07：Webhook 通道只加 adapter 与路由即可接入（真实 Gateway HTTP + 假 webhook + Stream + Runtime）。

不得 Mock 的真实边界：

- 真实 uvicorn 子进程里的 Gateway（真实 HTTP 路由、真实验签）与 Console/Runtime/Worker；
- 真实本地 HTTP 客户端扮演 **webhook 提供方**（HMAC 签名 + 时间戳）；
- 真实 Redis（`im.inbound:*` 逐条回读）与真实 PostgreSQL（Run 逐行回读）。

断言口径（scenarios.md S-07）：

1. 不新增 Deployment：通道接入只靠 adapter 配置 + `/channels/{channel}/events` 路由；
2. 合法事件**先持久化（XADD）再 ACK**，非法签名/过期时间戳**不落库、不建 Run**；
3. 重复 `event_id` 幂等：只有一个 Stream 条目、不会产生第二个 Run。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio
from muad_common import SharedSettings

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    GatewayStack,
    clear_engine_caches,
    latest_run_id,
    purge_tenant,
    require,
    start_gateway_stack,
    stop_gateway_stack,
    wait_for_new_run_terminal,
)

WEBHOOK_CHANNEL = "WECOM"
WEBHOOK_SECRET = "e2e-webhook-signing-secret"
WEBHOOK_PATH = f"/channels/{WEBHOOK_CHANNEL}/events"
#: 时间戳容差（与 Gateway 的 `WEBHOOK_TIMESTAMP_TOLERANCE_SEC` 默认值一致）。
TIMESTAMP_TOLERANCE_SEC = 300
#: 有界等待的长尾：等待“没有新 Run 诞生”的观察窗口。
NO_RUN_SETTLE_SEC = 2.0
ROOT = Path(__file__).resolve().parents[3]


def _webhook_env() -> dict[str, str]:
    return {
        "GATEWAY_WEBHOOK_CHANNELS": json.dumps(
            [{"channel": WEBHOOK_CHANNEL, "secret": WEBHOOK_SECRET, "reply_url": None}]
        ),
        "WEBHOOK_TIMESTAMP_TOLERANCE_SEC": str(TIMESTAMP_TOLERANCE_SEC),
    }


@pytest.fixture(scope="module")
async def stack(tmp_path_factory: pytest.TempPathFactory) -> Any:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("im-webhook-acceptance")
    gateway_stack, processes = start_gateway_stack(root, gateway_env=_webhook_env())
    try:
        yield gateway_stack
    finally:
        stop_gateway_stack(processes)
        await purge_tenant()
        clear_engine_caches()


def event_body(*, message_id: str | None = None, event_id: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "bot_id": BOT_ID,
        "external_user_id": BOUND_EXTERNAL_USER_ID,
        "external_conversation_id": "e2e-webhook-conversation",
        "message_id": message_id or f"e2e-webhook-{uuid.uuid4().hex}",
        "text": "hello from webhook",
    }


def signed_headers(
    secret: str, timestamp: int, raw: bytes, *, event_id: str | None = None
) -> dict[str, str]:
    message = f"{timestamp}.{raw.decode('utf-8')}".encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    headers = {
        "content-type": "application/json",
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature": signature,
    }
    if event_id is not None:
        headers["X-Webhook-Event-Id"] = event_id
    return headers


async def _redis() -> redis.asyncio.Redis:
    url = require("REDIS_URL", SharedSettings().redis_url)
    return redis.asyncio.Redis.from_url(url, decode_responses=True)


async def _inbound_entries(redis: redis.asyncio.Redis, *, event_id: str) -> list[tuple[str, dict[str, str]]]:
    """按 event_id 回读所有入站 Stream 条目（真实 Redis，不做进程内断言）。"""
    found: list[tuple[str, dict[str, str]]] = []
    for key in sorted(await redis.keys("im.inbound:*")):
        for entry_id, fields in await redis.xrange(key):
            raw = fields.get("event", "")
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if payload.get("event_id") == event_id:
                found.append((str(entry_id), payload))
    return found


async def _run_count() -> int:
    """本租户 Run 总数（真实 PG）。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t"),
                {"t": SharedSettings().default_tenant_id},
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def _post(
    stack: GatewayStack, body: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient(base_url=stack.gateway_url, timeout=10.0) as client:
        return await client.post(WEBHOOK_PATH, content=json.dumps(body), headers=headers)


@pytest.mark.asyncio
async def test_s07_valid_event_is_persisted_before_ack_and_creates_one_run(
    stack: GatewayStack,
) -> None:
    previous_run_id = await latest_run_id()
    body = event_body()
    raw = json.dumps(body).encode()
    headers = signed_headers(WEBHOOK_SECRET, int(time.time()), raw, event_id=body["event_id"])

    started = time.monotonic()
    response = await _post(stack, body, headers)
    elapsed = time.monotonic() - started

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["data"]["accepted"] is True
    assert payload["data"]["duplicate"] is False
    assert elapsed < 2.0, f"webhook ACK 必须快速（实际 {elapsed:.2f}s）"

    client = await _redis()
    try:
        entries = await asyncio.wait_for(
            _wait_for_entries(client, body["event_id"]), timeout=5.0
        )
    finally:
        await client.aclose()
    assert len(entries) == 1, "合法事件必须在 ACK 之前已经持久化到 im.inbound"
    entry_id, stored = entries[0]
    assert entry_id, "Stream 条目必须带真实 entry id"
    assert stored["external_message_id"] == body["message_id"]
    assert stored["channel"] == WEBHOOK_CHANNEL

    status = await wait_for_new_run_terminal(previous_run_id=previous_run_id, timeout=120.0)
    assert status in ("COMPLETED", "FAILED", "CANCELLED")
    run_id = await latest_run_id()
    assert run_id is not None and run_id != previous_run_id


async def _wait_for_entries(
    client: redis.asyncio.Redis, event_id: str
) -> list[tuple[str, dict[str, str]]]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        entries = await _inbound_entries(client, event_id=event_id)
        if entries:
            return entries
        await asyncio.sleep(0.05)
    return []


@pytest.mark.asyncio
async def test_s07_invalid_signature_is_rejected_without_persisting_or_running(
    stack: GatewayStack,
) -> None:
    previous_run_id = await latest_run_id()
    before = await _run_count()
    body = event_body()
    raw = json.dumps(body).encode()
    headers = signed_headers("wrong-secret", int(time.time()), raw, event_id=body["event_id"])

    response = await _post(stack, body, headers)

    assert response.status_code in (401, 403), response.text
    assert response.json()["code"] != "0"
    client = await _redis()
    try:
        assert await _inbound_entries(client, event_id=body["event_id"]) == []
    finally:
        await client.aclose()
    await asyncio.sleep(NO_RUN_SETTLE_SEC)
    assert await _run_count() == before
    assert await latest_run_id() == previous_run_id


@pytest.mark.asyncio
async def test_s07_expired_timestamp_is_rejected_without_persisting(
    stack: GatewayStack,
) -> None:
    body = event_body()
    raw = json.dumps(body).encode()
    stale = int(time.time()) - TIMESTAMP_TOLERANCE_SEC - 60
    headers = signed_headers(WEBHOOK_SECRET, stale, raw, event_id=body["event_id"])

    response = await _post(stack, body, headers)

    assert response.status_code in (401, 403), response.text
    client = await _redis()
    try:
        assert await _inbound_entries(client, event_id=body["event_id"]) == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_s07_duplicate_event_id_is_idempotent(stack: GatewayStack) -> None:
    previous_run_id = await latest_run_id()
    body = event_body()
    raw = json.dumps(body).encode()
    headers = signed_headers(WEBHOOK_SECRET, int(time.time()), raw, event_id=body["event_id"])

    first = await _post(stack, body, headers)
    assert first.status_code == 202, first.text
    await wait_for_new_run_terminal(previous_run_id=previous_run_id, timeout=120.0)
    run_id = await latest_run_id()
    runs_after_first = await _run_count()

    second = await _post(stack, body, headers)

    assert second.status_code == 202, second.text
    assert second.json()["data"]["duplicate"] is True
    client = await _redis()
    try:
        entries = await _inbound_entries(client, event_id=body["event_id"])
    finally:
        await client.aclose()
    assert len(entries) == 1, "重复 event_id 不得重复入队"
    await asyncio.sleep(NO_RUN_SETTLE_SEC)
    assert await _run_count() == runs_after_first, "重复 event_id 不得产生第二个 Run"
    assert await latest_run_id() == run_id


def test_s07_no_new_deployment_or_image_is_required() -> None:
    """通道接入 = adapter 配置 + 路由：部署单元集合不变，没有 webhook 专属 Deployment。"""
    deployments: list[str] = []
    for manifest in sorted((ROOT / "deploy" / "k8s" / "base").glob("*.yaml")):
        text = manifest.read_text(encoding="utf-8")
        if "kind: Deployment" not in text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("name: ") and "Deployment" in text:
                deployments.append(stripped.removeprefix("name: "))
    assert deployments, "未读到任何 Deployment 清单：路径规则可能已与目录结构脱节"
    assert not [name for name in deployments if "webhook" in name.lower()], (
        f"Webhook 通道不得新增部署单元：{deployments}"
    )
    gateway = (ROOT / "deploy" / "k8s" / "base" / "im-gateway.yaml").read_text(encoding="utf-8")
    assert "muad-im-gateway" in gateway
