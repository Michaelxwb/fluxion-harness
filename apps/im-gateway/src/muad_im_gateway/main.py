from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from fastapi import FastAPI
from muad_api import (
    install_api_foundation,
    install_health_probes,
    install_metrics,
    validate_startup,
)
from muad_api.catalog import MessageCatalog
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.delivery import router as delivery_router
from .api.health import readiness_checks, readiness_detail
from .application.bot_snapshot import BotSnapshotCache
from .application.console_client import ConsoleClient
from .application.inbound import InboundPipeline
from .application.inbound_attachments import InboundAttachmentStore
from .application.runtime_client import RuntimeClient
from .channels.base import ChannelRegistry
from .channels.probe import HttpProbeChannelAdapter
from .channels.wecom.adapter import WeComAdapter
from .infrastructure.dedupe import DedupeStore, build_dedupe_store

SERVICE_NAME = "muad-im-gateway"

configure_logging(SERVICE_NAME)


def _build_adapter(
    settings: SharedSettings, console: ConsoleClient
) -> WeComAdapter | HttpProbeChannelAdapter:
    if settings.channel_probe_url:
        # 本地真实 HTTP 探针：验收/联调环境替代第三方实网渠道。
        return HttpProbeChannelAdapter(settings.channel_probe_url)
    # 出站交付要按 storage_key 直读共享 store（字节不经核心域搬运，设计 §3.5）；
    # 不能直发时还要一条取件直链 —— 令牌的权威在 Console 进程里，只能经 `console` 要。
    return WeComAdapter(
        artifact_store=NfsArtifactStore(settings.artifact_root),
        fetch_links=console,
    )


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
    console = ConsoleClient(settings.console_platform_url)
    # 适配器先建，但它要用 console 当取件直链的签发口 ⇒ console 必须先于它存在。
    adapter = _build_adapter(settings, console)
    registry = ChannelRegistry()
    registry.register(adapter)
    dedupe = await build_dedupe_store(settings.redis_url)
    runtime = RuntimeClient(settings.agent_runtime_url)
    snapshot = BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        on_snapshot_changed=adapter.apply_snapshot,
    )
    # 附件字节落共享 artifact store（RWX PVC，Runtime 同挂）：部署侧必须挂载 `artifact_root`，
    # 只注入环境变量而不挂卷会让网关写进容器临时盘并"成功"，Runtime 却什么都读不到。
    inbound = InboundPipeline(
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        catalog=catalog,
        attachment_store=InboundAttachmentStore(NfsArtifactStore(settings.artifact_root)),
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
install_metrics(app)
