"""S-03: Cluster 模式下 Connector 租约 Lua 原子且可安全接管。

真实边界：两个真实 Connector 子进程 + 真实 3 节点 cluster Redis + 真实 Console 快照。
"""

from __future__ import annotations

import asyncio
import json
import time

from muad_common.redis_client import RedisClient

from tests.acceptance.im_gateway.connector_environment import BOT_ID, kill_process
from tests.acceptance.im_gateway.environment import TENANT

LEASE_KEY = f"connector:bot:{{{TENANT}:{BOT_ID}}}:lease"
EPOCH_KEY = f"connector:bot:{{{TENANT}:{BOT_ID}}}:epoch"


def _lease(raw: str | None) -> tuple[str, int] | None:
    if not raw:
        return None
    payload = json.loads(raw)
    return str(payload.get("owner_id") or ""), int(payload.get("epoch") or 0)


async def _wait_lease(
    client: RedisClient, *, timeout: float = 60.0
) -> tuple[str, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        parsed = _lease(await client.get(LEASE_KEY))
        if parsed is not None:
            return parsed
        await asyncio.sleep(0.2)
    raise AssertionError("没有 Connector 取得租约")


async def test_s03_cluster_connector_lease_takeover(
    cluster_client: RedisClient, connector_stack: object
) -> None:
    stack = connector_stack
    owner, epoch = await _wait_lease(cluster_client)
    assert owner in {"connector-0", "connector-1"}

    # 只有一个 owner：稳定窗口内租约不换主。
    await asyncio.sleep(3.0)
    assert _lease(await cluster_client.get(LEASE_KEY)) == (owner, epoch)
    # lease 与 epoch 同 slot：双 key Lua 能正常执行（否则会 CROSSSLOT）。
    assert await cluster_client.get(EPOCH_KEY) is not None

    loser = "connector-1" if owner == "connector-0" else "connector-0"
    kill_process(stack.processes[owner])  # SIGKILL：不优雅释放

    deadline = time.monotonic() + 40.0
    taken = None
    while time.monotonic() < deadline:
        current = _lease(await cluster_client.get(LEASE_KEY))
        if current and current[0] == loser and current[1] > epoch:
            taken = current
            break
        await asyncio.sleep(0.3)

    assert taken is not None, "接管后新 owner 未取得更高 epoch"
    assert taken[0] == loser
    assert taken[1] > epoch
