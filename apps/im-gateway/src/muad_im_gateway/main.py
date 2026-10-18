from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from fastapi import FastAPI
from muad_api import (
    install_api_foundation,
    install_health_probes,
    validate_startup,
)
from muad_api.catalog import MessageCatalog
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.delivery import router as delivery_router
from .api.health import readiness_checks, readiness_detail
from .application.bot_snapshot import BotSnapshotCache
from .application.channel_delivery import ActiveChannelDelivery, ConsoleDeliveryPermission
from .application.console_client import ConsoleClient
from .application.inbound import InboundPipeline
from .application.inbound_attachments import InboundAttachmentStore
from .application.runtime_client import RuntimeClient
from .channels.base import ChannelRegistry
from .channels.registry import build_channel_registry, dispatch_snapshot
from .infrastructure.artifact_resolver import HttpArtifactResolver
from .infrastructure.dedupe import DedupeStore, build_dedupe_store
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .metrics import install_gateway_metrics

SERVICE_NAME = "muad-im-gateway"

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

    def publish(self, app: FastAPI) -> None:
        """初始化成功后才公开运行时状态：探针与投递 API 都从这里读依赖。"""
        app.state.registry = self.registry
        app.state.dedupe_store = self.dedupe
        app.state.console_client = self.console
        app.state.runtime_client = self.runtime
        app.state.bot_snapshot = self.snapshot
        app.state.inbound_pipeline = self.inbound
        app.state.artifact_resolver = self.artifacts
        app.state.tenant_id = self.tenant_id
        app.state.chat_auth_recheck_sec = self.chat_auth_recheck_sec

    async def aclose(self) -> None:
        await self.registry.stop_all()
        await self.runtime.aclose()
        await self.console.aclose()
        await self.settings_client.aclose()
        await self.artifacts.aclose()
        await self.dedupe.aclose()


async def _build_resources(
    settings: SharedSettings, catalog: MessageCatalog
) -> _GatewayResources:
    console = ConsoleClient(settings.console_platform_url)
    # 标准适配器在注册表里装配（WECOM 探针/真机 + WEBCHAT 稳定入口）：装配根只调用这里，
    # 不按通道分支；适配器先建，但它要用 console 当取件直链的签发口 ⇒ console 必须先存在。
    registry = build_channel_registry(settings, console)
    dedupe = await build_dedupe_store(settings.redis_url)
    runtime = RuntimeClient(settings.agent_runtime_url)
    # 平台设置快照的唯一读取缝：每条入站消息的回复生命周期开始时取一次（ADR-04）。
    settings_client = ConsolePlatformSettingsClient(
        settings.console_platform_url, service_token=settings.internal_service_token
    )
    # 快照按 `BotSnapshotItem.channel` 分发到各适配器（设计 §3.3）：WEBCHAT 条目不会进企微
    # 的长连接/secret 路径。
    snapshot = BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        on_snapshot_changed=dispatch_snapshot(registry),
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
        settings_client=settings_client,
        # Run 终态交付的同键协调（设计 §3.6）：活跃会话收尾与兜底主动交付共用
        # `run:{run_id}:terminal`，经 Runtime 的受信 API-18 端口 reserve/ack（网关不持库）。
        channel_delivery=ActiveChannelDelivery(
            runtime, permission=ConsoleDeliveryPermission(console)
        ),
    )
    # 产物交付前按 `artifact_id` 解析归属（跨租户拿不到东西）：解析口径在 Runtime 那一处，
    # 网关只做调用方 —— 详见 `infrastructure/artifact_resolver.py` 的头注。
    artifacts = HttpArtifactResolver(
        settings.agent_runtime_url, service_token=settings.internal_service_token
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
install_gateway_metrics(app)
