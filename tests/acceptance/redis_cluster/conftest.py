"""S-02 集群验收基座：真实 3 节点 Redis Cluster（db 0）+ 真实 PostgreSQL。

集群只有 db 0，不能沿用 `tests/acceptance/conftest.py` 的 Redis DB 10–15 隔离，
因此这里覆盖同名 autouse fixture：连接本地集群（默认 `localdb` 的 6391–6393），
用例自身用唯一 key 前缀与收尾清理来隔离。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
import redis as redis_sync
from muad_common import SharedSettings, reset_settings_cache
from muad_common.redis_client import (
    RedisClient,
    create_redis_client,
    verify_redis_topology,
)
from redis.exceptions import RedisClusterException, RedisError

import tests.acceptance.im_gateway.environment as im_environment
from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    GatewayStack,
    clear_engine_caches,
    purge_tenant,
    require,
    start_gateway_stack,
    stop_gateway_stack,
)

CLUSTER_URL = os.environ.get("MUAD_CLUSTER_REDIS_URL", "redis://127.0.0.1:6391/0")
CLUSTER_SEEDS = os.environ.get(
    "MUAD_CLUSTER_SEEDS", "127.0.0.1:6391,127.0.0.1:6392,127.0.0.1:6393"
)
_MANAGED = (
    "REDIS_URL",
    "REDIS_MODE",
    "REDIS_CLUSTER_SEEDS",
    "INBOUND_STREAM_RETENTION_SEC",
)
_CLUSTER_MODULE_PREFIX = "test_cluster_"


@pytest.fixture(scope="module", autouse=True)
def isolated_datastores(request: pytest.FixtureRequest) -> Iterator[None]:
    """集群模块覆盖父级 DB 10–15 隔离（cluster 只支持 db 0）；S-01 沿用独立库。

    必须是 module 作用域：`os.environ` 是进程级的，session 作用域会把
    `REDIS_URL=cluster` 一直留到会话结束，污染其后按字母序执行的其它套件
    （实测整仓 acceptance 中 wecom_attachments 因此失败）。
    """
    module = request.module.__name__.rsplit(".", 1)[-1]
    if not module.startswith(_CLUSTER_MODULE_PREFIX):
        # standalone 模块由 `test_standalone_regression.standalone_env` 自行准备独立库。
        yield
        return

    saved = {key: os.environ.get(key) for key in _MANAGED}
    os.environ.update(
        {
            "REDIS_URL": CLUSTER_URL,
            "REDIS_MODE": "cluster",
            "REDIS_CLUSTER_SEEDS": CLUSTER_SEEDS,
            "INBOUND_STREAM_RETENTION_SEC": "60",
        }
    )
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


def _reset_cluster_state(redis_url: str | None = None) -> None:
    """集群版栈级清理：同步单机客户端在跨 slot 批量 DEL 时会 CROSSSLOT。"""
    if os.environ.get("MUAD_ACCEPTANCE_SHARED_DB") == "1":
        return
    target = redis_url or CLUSTER_URL
    parts = urlsplit(target)
    client = redis_sync.RedisCluster(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 6379,
        decode_responses=True,
    )
    try:
        for pattern in im_environment._STACK_REDIS_PATTERNS:
            batch: list[str] = []
            for key in client.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) >= 500:
                    client.delete(*batch)
                    batch = []
            if batch:
                client.delete(*batch)
    finally:
        client.close()


@pytest.fixture(scope="module")
async def cluster_client() -> AsyncIterator[RedisClient]:
    client = create_redis_client(
        CLUSTER_URL,
        mode="cluster",
        seeds=[seed for seed in CLUSTER_SEEDS.split(",") if seed],
        decode_responses=True,
    )
    try:
        await verify_redis_topology(client, mode="cluster")
    except (RedisError, RedisClusterException, OSError) as exc:
        await client.aclose()
        pytest.fail(
            "env_blocked: Redis Cluster 不可达 "
            f"({CLUSTER_URL}; seeds={CLUSTER_SEEDS}): {exc}. "
            "本地启动: docker compose -f /Users/jahan/workspace/localdb/docker-compose.yaml "
            "--profile redis-cluster up -d redis-cluster"
        )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(scope="module")
async def gateway_stack(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[GatewayStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("redis-cluster-acceptance")
    # 本用例只验证 Stream/key 契约，不依赖 Connector 长连接：用 webhook 适配器（reply_url=None）
    # 让 Gateway 自己消费入站事件，避免把“Connector 尚未迁移到集群客户端”的失败混进来。
    gateway_env = {
        "GATEWAY_WEBHOOK_CHANNELS": json.dumps(
            [{"channel": "WECOM", "secret": "cluster-e2e-secret", "reply_url": None}]
        ),
        "WEBHOOK_TIMESTAMP_TOLERANCE_SEC": "300",
    }
    with patch.object(im_environment, "reset_redis_state", _reset_cluster_state):
        stack, processes = start_gateway_stack(root, gateway_env=gateway_env)
    try:
        yield stack
    finally:
        stop_gateway_stack(processes)
        _reset_cluster_state()
        await purge_tenant()
        clear_engine_caches()

@pytest.fixture(scope="module")
async def connector_stack(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[object]:
    """两个真实 Connector 子进程 + 真实 Console 快照 + 真实 WS 探针（集群 Redis）。"""
    from tests.acceptance.im_gateway.connector_environment import (
        start_connector_stack,
        stop_connector_stack,
    )
    from tests.e2e.wecom_probe_app import WeComProbe

    root = tmp_path_factory.mktemp("redis-cluster-connector")
    probe = WeComProbe(expected_bots={BOT_ID: "e2e-im-bot-secret"})
    await probe.start()
    stack, processes = start_connector_stack(
        root,
        ws_probe_url=probe.ws_url,
        ws_ca_file=str(probe.cert_path),
        connectors=2,
    )
    stack.ws_probe = probe
    try:
        yield stack
    finally:
        stop_connector_stack(processes)
        _reset_cluster_state()
        await probe.stop()
        await purge_tenant()

