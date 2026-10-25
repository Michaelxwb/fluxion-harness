from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import redis.asyncio
from fastapi import FastAPI
from muad_api import (
    install_api_foundation,
    install_health_probes,
    validate_startup,
)
from muad_api.catalog import MessageCatalog
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_im_core import BotSnapshotCache, InboundPublisher
from muad_logging import configure_logging
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api.delivery import router as delivery_router
from .api.health import readiness_checks, readiness_detail
from .api.webchat import router as webchat_router
from .api.webhook import router as webhook_router
from .application.channel_delivery import ActiveChannelDelivery, ConsoleDeliveryPermission
from .application.console_client import ConsoleClient
from .application.inbound import InboundPipeline
from .application.inbound_attachments import InboundAttachmentStore
from .application.inbound_streams import InboundStreamConsumer
from .application.runtime_client import RuntimeClient
from .application.webchat_streams import SseBufferLedger
from .channels.base import ChannelRegistry
from .channels.registry import build_channel_registry, dispatch_snapshot
from .infrastructure.artifact_resolver import HttpArtifactResolver
from .infrastructure.dedupe import DedupeStore, build_dedupe_store
from .infrastructure.http_clients import GatewayHttpPools, build_gateway_http_pools
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .infrastructure.streams import DeliveryStreams
from .metrics import install_gateway_metrics

logger = logging.getLogger(__name__)

SERVICE_NAME = "muad-im-gateway"

#: 生命周期后台任务的装配根命名（可观测面：每条独立循环一条任务，退出即停）。
SNAPSHOT_LOOP_NAME = "gateway.snapshot"
INBOUND_LOOP_NAME_PREFIX = "gateway.inbound."
INBOUND_STREAM_LOOP_NAME = "gateway.inbound.stream"

configure_logging(SERVICE_NAME)


@dataclass
class _GatewayResources:
    """Gateway 运行时资源：初始化中途失败也要能回收已创建的部分。"""

    registry: ChannelRegistry
    dedupe: DedupeStore
    console: ConsoleClient
    settings_client: ConsolePlatformSettingsClient
    runtime: RuntimeClient
    snapshot: BotSnapshotCache
    inbound: InboundPipeline
    artifacts: HttpArtifactResolver
    tenant_id: str
    #: CHAT 长流复核周期（启动资源预算）：SSE 路由的复核定时器消费（TASK-044）。
    chat_auth_recheck_sec: float
    #: 出站分池（装配根建、装配根关；各客户端只借用）与共享存储/进程级 SSE 账本。
    pools: GatewayHttpPools
    artifact_store: NfsArtifactStore
    sse_ledger: SseBufferLedger
    #: 启动设置（预算/传输上限的唯一来源）：发布状态值时从这里读，不复制默认值。
    settings: SharedSettings
    #: 出站命令 Stream 与入站发布器（同一个 Redis 连接；Connector/Webhook 共用键口径）。
    streams: DeliveryStreams
    inbound_publisher: InboundPublisher
    inbound_consumer: InboundStreamConsumer

    def publish(self, app: FastAPI) -> None:
        """初始化成功后才公开运行时状态：探针与投递/聊天 API 都从这里读依赖。"""
        app.state.registry = self.registry
        app.state.dedupe_store = self.dedupe
        app.state.console_client = self.console
        app.state.runtime_client = self.runtime
        app.state.bot_snapshot = self.snapshot
        app.state.inbound_pipeline = self.inbound
        app.state.artifact_resolver = self.artifacts
        app.state.tenant_id = self.tenant_id
        app.state.chat_auth_recheck_sec = self.chat_auth_recheck_sec
        app.state.delivery_streams = self.streams
        app.state.delivery_stream_wait_sec = self.settings.delivery_stream_wait_sec
        app.state.inbound_publisher = self.inbound_publisher
        # Webchat 路由（TASK-052/054/055/057/061）消费的状态值：装配根是唯一发布点，
        # 值来自同一个 SharedSettings（预算/上限的唯一来源，不在路由层另抄一份默认值）。
        app.state.settings_client = self.settings_client
        app.state.artifact_store = self.artifact_store
        app.state.http_pools = self.pools
        app.state.webchat_sse_ledger = self.sse_ledger
        app.state.sse_event_max_bytes = self.settings.sse_event_max_bytes
        app.state.run_event_heartbeat_sec = self.settings.run_event_heartbeat_sec
        app.state.sse_subscriber_buffer_bytes = self.settings.sse_subscriber_buffer_bytes
        app.state.sse_total_buffer_bytes = self.settings.sse_total_buffer_bytes
        app.state.chat_message_hard_max_bytes = self.settings.chat_message_hard_max_bytes
        app.state.chat_upload_hard_max_bytes = self.settings.chat_upload_hard_max_bytes
        app.state.gateway_max_sse_subscribers = self.settings.gateway_max_sse_subscribers

    async def aclose(self) -> None:
        await self.inbound_consumer.stop()
        await self.registry.stop_all()
        await self.runtime.aclose()
        await self.console.aclose()
        await self.settings_client.aclose()
        await self.artifacts.aclose()
        await self.dedupe.aclose()
        await self.streams.aclose()


def _build_outbound_clients(
    settings: SharedSettings, pools: GatewayHttpPools
) -> tuple[ConsoleClient, RuntimeClient, ConsolePlatformSettingsClient, HttpArtifactResolver]:
    """出站客户端：全部消费装配根注入的分池（控制/stream/上传/业务各自独立预算）。"""
    console = ConsoleClient(settings.console_platform_url, client=pools.control)
    runtime = RuntimeClient(
        settings.agent_runtime_url,
        client=pools.egress,
        stream_client=pools.stream,
        upload_client=pools.upload,
    )
    # 平台设置快照的唯一读取缝：每条入站消息的回复生命周期开始时取一次（ADR-04）。
    settings_client = ConsolePlatformSettingsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    # 产物交付前按 `artifact_id` 解析归属（跨租户拿不到东西）：解析口径在 Runtime 那一处，
    # 网关只做调用方 —— 详见 `infrastructure/artifact_resolver.py` 的头注。
    artifacts = HttpArtifactResolver(
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        client=pools.egress,
    )
    return console, runtime, settings_client, artifacts


def _build_redis(settings: SharedSettings) -> redis.asyncio.Redis:
    """Gateway 的 Redis 连接：入站/出站 Stream 是 IM ASYNC 路径的持久实时面。"""
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required for the IM inbound/outbound streams")
    client: redis.asyncio.Redis = redis.asyncio.Redis.from_url(
        settings.redis_url, decode_responses=True
    )
    return client


def _build_inbound(
    settings: SharedSettings,
    catalog: MessageCatalog,
    *,
    dedupe: DedupeStore,
    console: ConsoleClient,
    runtime: RuntimeClient,
    settings_client: ConsolePlatformSettingsClient,
    artifact_store: NfsArtifactStore,
) -> InboundPipeline:
    """入站管线：附件落共享存储、终态交付同键协调、受理上限取启动预算（§3.6/§3.7）。"""
    return InboundPipeline(
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        catalog=catalog,
        attachment_store=InboundAttachmentStore(artifact_store),
        tenant_id=settings.default_tenant_id,
        settings_client=settings_client,
        # Run 终态交付的同键协调（设计 §3.6）：活跃会话收尾与兜底主动交付共用
        # `run:{run_id}:terminal`，经 Runtime 的受信 API-18 端口 reserve/ack（网关不持库）。
        channel_delivery=ActiveChannelDelivery(
            runtime, permission=ConsoleDeliveryPermission(console)
        ),
        # 在飞受理上限：每进程启动预算（§3.7 GATEWAY_MAX_CONCURRENT_HANDLERS）。
        max_concurrent_handlers=settings.gateway_max_concurrent_handlers,
        # 排队上限（全局/每路由/命令三条）：同一处启动预算（§3.7 GATEWAY_MAX_PENDING_*）。
        max_pending_handlers=settings.gateway_max_pending_handlers,
        max_pending_per_route=settings.gateway_max_pending_per_route,
        max_pending_commands=settings.gateway_max_pending_commands,
    )


def _build_snapshot(
    settings: SharedSettings, console: ConsoleClient, registry: ChannelRegistry
) -> BotSnapshotCache:
    """入口快照：按 `BotSnapshotItem.channel` 分发到各适配器（设计 §3.3）。

    **只请求本 Gateway 拥有通道的条目**（NFR-SEC-01）：企微等通道的 bot secret 只流向
    Connector，Gateway 的响应/日志/内存里都不该出现它。节拍来自 typed 启动设置。
    """
    return BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        channels=frozenset(registry.names()),
        # 请求面不需要连接凭据：通道过滤只缩小范围，secret 的下发必须单独关掉。
        include_secret=False,
        interval_sec=settings.bot_snapshot_poll_sec,
        on_snapshot_changed=dispatch_snapshot(registry),
    )


async def _build_resources(
    settings: SharedSettings, catalog: MessageCatalog, pools: GatewayHttpPools
) -> _GatewayResources:
    """按启动设置组装运行时资源；中途失败时回收已创建的部分（分池由调用方收尾）。"""
    console, runtime, settings_client, artifacts = _build_outbound_clients(settings, pools)
    # 请求面只装配不含渠道长连接/私钥的适配器（WEBCHAT + 配置驱动的通用 webhook）；
    # 有状态通道（企微）的适配器活在 Connector。装配根不按通道分支。
    registry = build_channel_registry(settings)
    redis = _build_redis(settings)
    dedupe = await build_dedupe_store(settings.redis_url)
    try:
        snapshot = _build_snapshot(settings, console, registry)
        # 附件字节落共享 artifact store（RWX PVC，Runtime 同挂）：部署侧必须挂载 `artifact_root`，
        # 只注入环境变量而不挂卷会让网关写进容器临时盘并"成功"，Runtime 却什么都读不到。
        artifact_store = NfsArtifactStore(settings.artifact_root)
        inbound = _build_inbound(
            settings,
            catalog,
            dedupe=dedupe,
            console=console,
            runtime=runtime,
            settings_client=settings_client,
            artifact_store=artifact_store,
        )
        # 公开 SSE 的进程级账本：单订阅、进程总缓冲与订阅者数量上限共用同一本账（TASK-061/096）。
        ledger = SseBufferLedger(
            total_bytes=settings.sse_total_buffer_bytes,
            max_subscribers=settings.gateway_max_sse_subscribers,
        )
        streams = DeliveryStreams(redis, maxlen=settings.inbound_stream_maxlen)
        publisher = InboundPublisher(
            redis,
            shards=settings.inbound_stream_shards,
            stream_maxlen=settings.inbound_stream_maxlen,
            lost_after_sec=settings.inbound_stream_lost_after_sec,
        )
        consumer = InboundStreamConsumer(
            redis,
            registry=registry,
            pipeline=inbound,
            shards=settings.inbound_stream_shards,
            max_inflight=settings.gateway_max_pending_handlers,
        )
        return _GatewayResources(
            registry=registry,
            dedupe=dedupe,
            console=console,
            settings_client=settings_client,
            runtime=runtime,
            snapshot=snapshot,
            inbound=inbound,
            artifacts=artifacts,
            tenant_id=settings.default_tenant_id,
            chat_auth_recheck_sec=settings.chat_auth_recheck_sec,
            pools=pools,
            artifact_store=artifact_store,
            sse_ledger=ledger,
            settings=settings,
            streams=streams,
            inbound_publisher=publisher,
            inbound_consumer=consumer,
        )
    except BaseException:
        # 半初始化不发布、也不留资源：自有的 dedupe/streams（Redis 连接）在这里回收；HTTP
        # 客户端只借用分池（调用方关闭池即收尾），注册表尚未 start（无连接可停）。
        await dedupe.aclose()
        await redis.aclose()
        raise


def _start_background(resources: _GatewayResources) -> list[asyncio.Task[None]]:
    """启动四类生命周期里的后台循环：快照刷新与**每适配器**入站消费各自独立成任务。

    机器人连接由注册表 `start_all`/`stop_all` 启停；公开 SSE 订阅由进程账本登记/释放。
    每条循环一条任务、命名可观测；退出路径在 lifespan 统一取消并等待收尾（不吞异常）。
    """
    tasks = [
        asyncio.create_task(resources.snapshot.run_forever(), name=SNAPSHOT_LOOP_NAME)
    ]
    tasks.extend(
        asyncio.create_task(
            resources.inbound.consume(adapter),
            name=f"{INBOUND_LOOP_NAME_PREFIX}{adapter.name}",
        )
        for adapter in resources.registry.started_adapters
    )
    # 入站 Stream 消费组：Connector/Webhook 持久化后的统一入口（IM ASYNC 路径）。
    tasks.append(
        asyncio.create_task(
            resources.inbound_consumer.run_forever(), name=INBOUND_STREAM_LOOP_NAME
        )
    )
    return tasks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    # Gateway 不持有数据库：只校验配置与 Artifact 挂载（迁移到 head 由持库服务校验）
    await validate_startup(settings, None, None, migrations_dir=None)
    # 出站分池：预算唯一来源是启动设置（§3.7），装配根建、装配根关，调用方只借用。
    pools = build_gateway_http_pools(settings)
    resources: _GatewayResources | None = None
    background: list[asyncio.Task[None]] = []
    # 初始化失败（快照不可用、连接管理器起不来）同样要回收已创建资源与分池
    try:
        resources = await _build_resources(settings, app.state.message_catalog, pools)
        await resources.snapshot.refresh()
        await resources.registry.start_all()
        resources.publish(app)
        background = _start_background(resources)
        yield
    finally:
        for task in background:
            task.cancel()
        try:
            for task in background:
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            if resources is not None:
                await resources.aclose()
            await pools.aclose()


app = FastAPI(title="MUAD IM Gateway", version="0.1.0", lifespan=lifespan)


class _ServerErrorLogMiddleware:
    """纯 ASGI 5xx 观测：只记录路径与状态，不缓冲响应（SSE 长流不受影响）。

    AppError 由 api-kit 的处理器变成封套后不会再留日志；没有这一层，未知路径的 5xx
    在日志里完全不可见（2026-10-20 容量压测排查花了一整轮）。
    """

    def __init__(self, asgi_app: ASGIApp) -> None:
        self._app = asgi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] >= 500:
                logger.warning(
                    "gateway_5xx", extra={"path": scope["path"], "status": message["status"]}
                )
            await send(message)

        await self._app(scope, receive, send_wrapper)


install_api_foundation(app)
install_health_probes(
    app,
    readiness_checks(app),
    detail=lambda: readiness_detail(app),
)
app.include_router(delivery_router)
# 通用 Webhook 入站（S-07）：`/channels/{channel}/events` —— 通道由配置装配，不新增部署单元。
app.include_router(webhook_router)
# Webchat 公开面（TASK-052/054/055/057/061）：HTTP 聊天/上传/下载与公开 SSE 的实际注册点。
app.include_router(webchat_router)
install_gateway_metrics(app)
# 最后注册 = 最外层：必须包在 api-kit 的异常/封套中间件之外，才能看到最终的 5xx 状态。
app.add_middleware(_ServerErrorLogMiddleware)
