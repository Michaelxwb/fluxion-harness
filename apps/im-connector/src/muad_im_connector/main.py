"""Connector 进程入口：只暴露 `/healthz`、`/readyz`、`/metrics`。

没有公开业务路由：终端用户的 HTTP 只打到 Gateway，Connector 只持有渠道长连接并消费
Redis Stream。探针/指标端口是运维面，不是业务面（design §4.1「不暴露公开 HTTP」）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from muad_api import install_health_probes
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_im_core import BotSnapshotCache, InboundPublisher
from muad_logging import configure_logging
from redis.asyncio import Redis

from .channels.wecom.adapter import WeComAdapter
from .lease import RedisLeaseManager
from .metrics import install_connector_metrics
from .outbound import CommandExecutor, OutboundRunner
from .reply_store import ReplyStore
from .runtime import ConnectorRuntime
from .settings import connector_settings

logger = logging.getLogger(__name__)

SERVICE_NAME = "muad-im-connector"


@dataclass
class ConnectorResources:
    redis: Redis
    console: object
    adapter: object
    runtime: ConnectorRuntime


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required but not configured")
    return value


def _build_resources(settings: SharedSettings) -> ConnectorResources:
    from muad_im_gateway.application.console_client import ConsoleClient

    connector = connector_settings()
    redis = Redis.from_url(
        _require(settings.redis_url, "REDIS_URL"),
        decode_responses=True,
        max_connections=64,
    )
    logger.info(
        "connector_redis_configured db=%s owner=%s",
        redis.connection_pool.connection_kwargs.get("db"),
        connector.owner_id,
    )
    console = ConsoleClient(
        _require(settings.console_platform_url, "CONSOLE_PLATFORM_URL"),
        service_token=settings.internal_service_token,
    )
    artifact_store = NfsArtifactStore(settings.artifact_root)
    adapter = WeComAdapter(
        artifact_store=artifact_store,
        fetch_links=console,
        status_updates_per_second=settings.im_progress_updates_per_second,
    )
    leases = RedisLeaseManager(
        redis, owner_id=connector.owner_id, ttl_sec=connector.connector_lease_ttl_sec
    )
    replies = ReplyStore(redis)
    publisher = InboundPublisher(
        redis,
        shards=connector.connector_inbound_shards,
        stream_maxlen=connector.connector_inbound_stream_maxlen,
        lost_after_sec=connector.connector_inbound_lost_after_sec,
        pause_before_xadd_sec=connector.connector_test_pause_before_xadd_sec,
    )
    executor = CommandExecutor(
        adapter=adapter, leases=leases, replies=replies, owner_id=connector.owner_id
    )
    outbound = OutboundRunner(
        redis,
        executor=executor,
        leases=leases,
        batch=connector.connector_outbound_batch,
        max_age_sec=connector.connector_command_max_age_sec,
        max_attempts=connector.connector_command_max_attempts,
    )
    holder: dict[str, ConnectorRuntime] = {}
    snapshot = BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        # 只取本角色接管的通道：企微 secret 不流向 Gateway，反之亦然（NFR-SEC-01）。
        channels=connector.handled_channels,
        interval_sec=settings.bot_snapshot_poll_sec,
        on_snapshot_changed=lambda items: holder["runtime"].on_snapshot(items),
    )
    runtime = ConnectorRuntime(
        tenant_id=settings.default_tenant_id,
        handled_channels=connector.handled_channels,
        adapter=adapter,
        leases=leases,
        replies=replies,
        publisher=publisher,
        outbound=outbound,
        snapshot=snapshot,
        renew_sec=connector.connector_lease_renew_sec,
    )
    holder["runtime"] = runtime
    # 适配器的原始消息回调必须在 runtime 建好之后绑定（runtime 持有有界队列）。
    adapter.set_raw_sink(runtime.raw_sink)
    return ConnectorResources(
        redis=redis, console=console, adapter=adapter, runtime=runtime
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    resources = _build_resources(settings)
    app.state.resources = resources
    await resources.runtime.start()
    try:
        yield
    finally:
        await resources.runtime.stop()
        await resources.console.aclose()  # type: ignore[attr-defined]
        await resources.redis.aclose()


configure_logging(SERVICE_NAME)
app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)


async def _redis_check() -> bool:
    resources: ConnectorResources | None = getattr(app.state, "resources", None)
    if resources is None:
        return False
    try:
        return bool(await resources.redis.ping())
    except Exception:  # noqa: BLE001 - 探针只回答"就绪/不就绪"
        return False


def _snapshot_check() -> bool:
    resources: ConnectorResources | None = getattr(app.state, "resources", None)
    return bool(resources and resources.runtime.ready)


def _detail() -> dict[str, object]:
    resources: ConnectorResources | None = getattr(app.state, "resources", None)
    return resources.runtime.readiness_detail() if resources else {}


install_health_probes(
    app,
    {"redis": _redis_check, "snapshot": lambda: _snapshot_check()},
    detail=_detail,
)
install_connector_metrics(app)
