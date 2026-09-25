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
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.admin_schedules import router as admin_schedules_router
from .api.admin_tasks import router as admin_tasks_router
from .api.schedules import router as schedules_router
from .api.tasks import router as tasks_router
from .delivery.client import HttpDeliveryClient
from .delivery.service import DeliveryLoop
from .infrastructure.cancel_hint import create_cancel_hint_store
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.wakeup_hint import (
    RedisWakeupNotifier,
    create_wakeup_listener,
    create_wakeup_notifier,
)
from .metrics import install_worker_metrics
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
    wakeup_listener = await create_wakeup_listener(settings.redis_url)
    cancel_hints = await create_cancel_hint_store(settings.redis_url)
    app.state.cancel_hints = cancel_hints
    # Redis 只是低延迟 hint：不可用时降级为 PG 扫描，不阻塞就绪；仅作为诊断详情上报。
    app.state.wakeup_mode = (
        "redis" if isinstance(wakeup_notifier, RedisWakeupNotifier) else "disabled"
    )
    async with httpx.AsyncClient() as http_client:
        worker = WorkerLoop(
            session_factory, settings, cancel_hints=cancel_hints, wakeup=wakeup_listener
        )
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
            await wakeup_listener.aclose()
            await cancel_hints.aclose()
    await dispose_engine()


def artifact_storage_readiness() -> bool:
    """Worker 需要 NFS/Artifact 根目录可挂载；Skill 执行与缓存都依赖它。"""
    return NfsArtifactStore(SharedSettings().artifact_root).root.is_dir()


def wakeup_detail() -> dict[str, str]:
    return {
        "wakeup_hint": getattr(app.state, "wakeup_mode", "uninitialized"),
        "artifact_root": SharedSettings().artifact_root,
    }


app = FastAPI(title="MUAD Agent Worker", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
install_worker_metrics(app)
install_health_probes(
    app,
    {
        "database": database_readiness(get_engine),
        "artifact_storage": artifact_storage_readiness,
    },
    detail=wakeup_detail,
)
app.include_router(tasks_router)
app.include_router(schedules_router)
app.include_router(admin_tasks_router)
app.include_router(admin_schedules_router)
