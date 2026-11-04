"""S-01: standalone 回归 —— 五进程真实栈 + 三族 key 布局（含 hash tag）。

真实边界：真实 Console / Runtime×2 / Worker / Gateway 子进程 + 真实 standalone Redis +
真实 PostgreSQL；不 mock 业务边界。集群路径已由 S-02..S-06 覆盖，本用例只证明默认
`REDIS_MODE=standalone` 的行为与 key 布局没有因集群改造回退。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import redis.asyncio as redis_async
from muad_common import SharedSettings, reset_settings_cache
from muad_contracts.im import (
    InboundEvent,
    inbound_stream_key_for_shard,
    lease_epoch_key,
    lease_key,
    outbound_stream_key_for,
)
from redis.crc import key_slot

from tests.acceptance.datastores import create_datastore, drop_datastore
from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    TENANT,
    GatewayStack,
    clear_engine_caches,
    count_tenant_rows,
    purge_tenant,
    start_gateway_stack,
    stop_gateway_stack,
)

pytestmark = pytest.mark.integration

_MANAGED = ("DATABASE_URL", "REDIS_URL", "REDIS_MODE", "REDIS_CLUSTER_SEEDS")
_INBOUND_GROUP_WAIT_SEC = 120.0
_MOVED_MARKERS = ("MovedError", "CROSSSLOT", "CLUSTERDOWN")


def _standalone_base_url() -> str:
    return os.environ.get("MUAD_STANDALONE_REDIS_URL", "redis://127.0.0.1:6379/0")


def _ambient_isolation_is_standalone() -> bool:
    database_url = SharedSettings().database_url or ""
    return (
        os.environ.get("REDIS_MODE", "standalone") != "cluster"
        and ("muad_acc_" in database_url or "muad_pst_" in database_url)
    )


@pytest.fixture(scope="module")
def standalone_env() -> Iterator[None]:
    """本模块固定 standalone 环境。

    单独跑本文件时 conftest 已按父级隔离建好独立库；整目录/整仓混跑时环境可能是集群，
    这里显式切回 standalone，并在没有独立库时自建一份（收尾丢弃）。
    """
    saved = {key: os.environ.get(key) for key in _MANAGED}
    created: dict[str, str] | None = None
    try:
        if not _ambient_isolation_is_standalone():
            os.environ["REDIS_MODE"] = "standalone"
            os.environ["REDIS_CLUSTER_SEEDS"] = ""
            os.environ["REDIS_URL"] = _standalone_base_url()
            reset_settings_cache()
            if (
                os.environ.get("MUAD_ACCEPTANCE_SHARED_DB") != "1"
                and SharedSettings().database_url
            ):
                created = create_datastore()
                os.environ["DATABASE_URL"] = created["database_url"]
                os.environ["REDIS_URL"] = created["redis_url"]
        os.environ["REDIS_MODE"] = "standalone"
        os.environ["REDIS_CLUSTER_SEEDS"] = ""
        reset_settings_cache()
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()
        if created is not None:
            drop_datastore(
                created["name"], created["admin_database_url"], created["redis_url"]
            )


@pytest.fixture(scope="module")
async def standalone_stack(
    standalone_env: None, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[GatewayStack]:
    root = tmp_path_factory.mktemp("redis-standalone-regression")
    gateway_env = {
        "GATEWAY_WEBHOOK_CHANNELS": json.dumps(
            [{"channel": "WECOM", "secret": "standalone-e2e-secret", "reply_url": None}]
        ),
        "WEBHOOK_TIMESTAMP_TOLERANCE_SEC": "300",
    }
    stack, processes = start_gateway_stack(root, gateway_env=gateway_env)
    try:
        yield stack
    finally:
        stop_gateway_stack(processes)
        await purge_tenant()
        clear_engine_caches()


def _event(stack: GatewayStack, message_id: str) -> InboundEvent:
    return InboundEvent(
        event_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT_ID,
        external_message_id=message_id,
        external_user_id=stack.bound_external_user_id,
        external_conversation_id=stack.chat_id,
        text="standalone regression",
        received_at=datetime.now(UTC),
        reply_ref=f"standalone-{message_id}",
    )


async def _wait_for_runs(expected: int, *, timeout: float = _INBOUND_GROUP_WAIT_SEC) -> int:
    deadline = time.monotonic() + timeout
    current = 0
    while time.monotonic() < deadline:
        current = await count_tenant_rows("runtime.run_record")
        if current >= expected:
            return current
        await asyncio.sleep(0.2)
    return current


def _assert_key_layout() -> None:
    inbound_slots = {
        key_slot(inbound_stream_key_for_shard(shard).encode()) for shard in range(32)
    }
    assert len(inbound_slots) == 1, f"32 个入站 shard 必须同 slot: {sorted(inbound_slots)}"

    outbound_slots = {
        key_slot(outbound_stream_key_for(TENANT, bot).encode())
        for bot in ("bot-a", "bot-b")
    }
    assert len(outbound_slots) == 1, f"同租户出站流必须同 slot: {sorted(outbound_slots)}"

    lease_slot = key_slot(lease_key(TENANT, BOT_ID).encode())
    epoch_slot = key_slot(lease_epoch_key(TENANT, BOT_ID).encode())
    assert lease_slot == epoch_slot, "lease 与 epoch 必须同 slot（Lua 同键集）"


def _assert_no_cluster_errors(logs_root: Path) -> None:
    for log_path in sorted(logs_root.glob("*.log")):
        text = log_path.read_text(encoding="utf-8", errors="replace")
        hits = [marker for marker in _MOVED_MARKERS if marker in text]
        assert not hits, f"{log_path.name} 出现集群专用错误 {hits}"


async def test_s01_standalone_five_process_stack_and_key_layout(
    standalone_stack: GatewayStack,
) -> None:
    stack = standalone_stack
    expected_services = {"console", "runtime", "runtime-2", "worker", "gateway"}
    assert expected_services <= set(stack.processes), (
        f"五进程未全部启动: {sorted(stack.processes)}"
    )

    async with httpx.AsyncClient() as http:
        response = await http.get(f"{stack.gateway_url}/readyz", timeout=10.0)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] == "ready"
    assert payload["redis_mode"] == "standalone"

    _assert_key_layout()

    client = redis_async.from_url(
        SharedSettings().require_redis_url(), decode_responses=True
    )
    try:
        stream_key = inbound_stream_key_for_shard(0)
        await client.xadd(
            stream_key,
            {"event": _event(stack, f"standalone-{uuid.uuid4().hex}").model_dump_json()},
            maxlen=5_000,
        )
        assert await client.exists(stream_key) == 1
    finally:
        await client.aclose()

    runs = await _wait_for_runs(1)
    assert runs >= 1, "standalone 栈未消费带 hash tag 的入站事件"

    _assert_no_cluster_errors(Path(stack.processes["gateway"].log_path).parent)
