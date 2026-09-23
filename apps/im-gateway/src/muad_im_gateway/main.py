from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from fastapi import FastAPI
from muad_api import install_api_foundation, install_health_probes, validate_startup
from muad_api.catalog import MessageCatalog
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.delivery import router as delivery_router
from .api.health import readiness_checks, readiness_detail
from .application.bot_snapshot import BotSnapshotCache
from .application.console_client import ConsoleClient
from .application.inbound import InboundPipeline
from .application.runtime_client import RuntimeClient
from .channels.base import ChannelRegistry
from .channels.probe import HttpProbeChannelAdapter
from .channels.wecom.adapter import WeComAdapter
from .infrastructure.dedupe import DedupeStore, build_dedupe_store

SERVICE_NAME = "muad-im-gateway"

configure_logging(SERVICE_NAME)


def _build_adapter(settings: SharedSettings) -> WeComAdapter | HttpProbeChannelAdapter:
    if settings.channel_probe_url:
        # 本地真实 HTTP 探针：验收/联调环境替代第三方实网渠道。
        return HttpProbeChannelAdapter(settings.channel_probe_url)
    return WeComAdapter()


@dataclass
class _GatewayResources:
    """Gateway 运行时资源：初始化中途失败也要能回收已创建的部分。"""

    registry: ChannelRegistry
    dedupe: DedupeStore
    console: ConsoleClient
    runtime: RuntimeClient
    snapshot: BotSnapshotCache
    inbound: InboundPipeline
    adapter: WeComAdapter | HttpProbeChannelAdapter

    def publish(self, app: FastAPI) -> None:
        """初始化成功后才公开运行时状态：探针与投递 API 都从这里读依赖。"""
        app.state.registry = self.registry
        app.state.dedupe_store = self.dedupe
        app.state.console_client = self.console
        app.state.runtime_client = self.runtime
        app.state.bot_snapshot = self.snapshot
        app.state.inbound_pipeline = self.inbound
        app.state.wecom_adapter = self.adapter

    async def aclose(self) -> None:
        await self.registry.stop_all()
        await self.runtime.aclose()
        await self.console.aclose()
        await self.dedupe.aclose()


async def _build_resources(
    settings: SharedSettings, catalog: MessageCatalog
) -> _GatewayResources:
    adapter = _build_adapter(settings)
    registry = ChannelRegistry()
    registry.register(adapter)
    dedupe = await build_dedupe_store(settings.redis_url)
    console = ConsoleClient(settings.console_platform_url)
    runtime = RuntimeClient(settings.agent_runtime_url)
    snapshot = BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        on_snapshot_changed=adapter.apply_snapshot,
    )
    inbound = InboundPipeline(
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=settings.default_tenant_id,
        locale=settings.default_locale,
    )
    return _GatewayResources(
        registry=registry,
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        snapshot=snapshot,
        inbound=inbound,
        adapter=adapter,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    # Gateway 不持有数据库：只校验配置与 Artifact 挂载（迁移到 head 由持库服务校验）
    await validate_startup(settings, None, None, migrations_dir=None)
    resources = await _build_resources(settings, app.state.message_catalog)
    background: list[asyncio.Task[None]] = []
    # 初始化失败（快照不可用、连接管理器起不来）同样要回收已创建资源
    try:
        await resources.snapshot.refresh()
        await resources.registry.start_all()
        resources.publish(app)
        background.append(asyncio.create_task(resources.snapshot.run_forever()))
        background.extend(
            asyncio.create_task(resources.inbound.consume(adapter))
            for adapter in resources.registry.started_adapters
        )
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task
        await resources.aclose()


app = FastAPI(title="MUAD IM Gateway", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
install_health_probes(
    app,
    readiness_checks(app),
    detail=lambda: readiness_detail(app),
)
app.include_router(delivery_router)
