from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from muad_api import (
    database_readiness,
    install_api_foundation,
    install_health_probes,
    validate_startup,
)
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.schedules import router as schedules_router
from .api.tasks import router as tasks_router
from .delivery.client import HttpDeliveryClient
from .delivery.service import DeliveryLoop
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.wakeup_hint import create_wakeup_notifier
from .scheduler.client import ConsoleResolveClient
from .scheduler.service import SchedulerLoop
from .worker.service import WorkerLoop

SERVICE_NAME = "muad-agent-worker"

configure_logging(SERVICE_NAME)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    await validate_startup(
        settings,
        get_engine(),
        None,
        migrations_dir=settings.migrations_dir,
    )
    session_factory = get_session_factory()
    wakeup_notifier = await create_wakeup_notifier(settings.redis_url)
    app.state.wakeup_notifier = wakeup_notifier
    async with httpx.AsyncClient() as http_client:
        worker = WorkerLoop(session_factory, settings)
        scheduler = SchedulerLoop(
            session_factory,
            ConsoleResolveClient(settings.console_platform_url, http_client),
            settings,
        )
        delivery = DeliveryLoop(
            session_factory,
            HttpDeliveryClient(settings.im_gateway_url, http_client),
            settings,
        )
        background = [
            asyncio.create_task(worker.run_forever()),
            asyncio.create_task(scheduler.run_forever()),
            asyncio.create_task(delivery.run_forever()),
        ]
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with suppress(asyncio.CancelledError):
                    await task
            await wakeup_notifier.aclose()
    await dispose_engine()


app = FastAPI(title="MUAD Agent Worker", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
install_health_probes(app, {"database": database_readiness(get_engine)})
app.include_router(tasks_router)
app.include_router(schedules_router)
