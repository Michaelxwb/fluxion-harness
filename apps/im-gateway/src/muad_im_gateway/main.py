from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.delivery import router as delivery_router
from .api.health import router as health_router
from .application.bot_snapshot import BotSnapshotCache
from .application.console_client import ConsoleClient
from .application.inbound import InboundPipeline
from .application.runtime_client import RuntimeClient
from .channels.base import ChannelRegistry
from .channels.wecom.adapter import WeComAdapter
from .infrastructure.dedupe import build_dedupe_store

SERVICE_NAME = "muad-im-gateway"

configure_logging(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    registry = ChannelRegistry()
    wecom = WeComAdapter()
    registry.register(wecom)
    dedupe = await build_dedupe_store(settings.redis_url)
    console = ConsoleClient(settings.console_platform_url)
    runtime = RuntimeClient(settings.agent_runtime_url)
    snapshot = BotSnapshotCache(
        console,
        tenant_id=settings.default_tenant_id,
        on_snapshot_changed=wecom.apply_snapshot,
    )
    inbound = InboundPipeline(
        dedupe=dedupe,
        console=console,
        runtime=runtime,
        catalog=app.state.message_catalog,
        tenant_id=settings.default_tenant_id,
        locale=settings.default_locale,
    )
    app.state.registry = registry
    app.state.dedupe_store = dedupe
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.bot_snapshot = snapshot
    app.state.inbound_pipeline = inbound
    app.state.wecom_adapter = wecom
    await snapshot.refresh()
    await registry.start_all()
    background: list[asyncio.Task[None]] = [asyncio.create_task(snapshot.run_forever())]
    background.extend(
        asyncio.create_task(inbound.consume(adapter)) for adapter in registry.started_adapters
    )
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task
        await registry.stop_all()
        await runtime.aclose()
        await console.aclose()
        await dedupe.aclose()


app = FastAPI(title="MUAD IM Gateway", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
app.include_router(health_router)
app.include_router(delivery_router)
