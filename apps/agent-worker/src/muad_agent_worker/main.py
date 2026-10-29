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
from muad_common.db_counters import publish_db_counters
from muad_logging import configure_logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api.admin_results import router as admin_results_router
from .api.admin_schedules import router as admin_schedules_router
from .api.admin_tasks import router as admin_tasks_router
from .api.runtime_operations import router as runtime_operations_router
from .api.schedules import router as schedules_router
from .api.tasks import router as tasks_router
from .application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from .delivery.artifact_client import ArtifactResolveClient
from .delivery.client import HttpDeliveryClient, HttpWebchatInboxClient
from .delivery.service import DeliveryLoop
from .infrastructure.cancel_hint import CancelHintStore, create_cancel_hint_store
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.http_clients import WorkerHttpPools, build_worker_http_pools
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .infrastructure.wakeup_hint import (
    RedisWakeupNotifier,
    WakeupListener,
    create_wakeup_listener,
    create_wakeup_notifier,
)
from .metrics import install_worker_metrics, record_gauge
from .results.dispatcher import ResultDispatcher, ResultDispatchPolicy
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
    app.state.wakeup_mode = "redis" if isinstance(wakeup_notifier, RedisWakeupNotifier) else "disabled"
    # 出站分池：预算唯一来源是启动设置（§3.7），装配根建、装配根关，调用方只借用（TASK-095）。
    pools = build_worker_http_pools(settings)
    app.state.http_pools = pools
    # 平台设置快照：任务创建/扇出与投递尝试两个边界各取一次（TASK-006），走控制池。
    settings_client = ConsolePlatformSettingsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    app.state.platform_settings_client = settings_client
    background: list[asyncio.Task[None]] = []
    try:
        background = _start_background(
            app,
            session_factory,
            settings,
            settings_client,
            pools,
            cancel_hints,
            wakeup_listener,
        )
        background.append(
            asyncio.create_task(_db_counters_loop(), name="worker.db_counters")
        )
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
        await settings_client.aclose()
        # 退出后不留真实 Setting 源：同进程再次进入（测试/多次 lifespan）回落到空对象，
        # 否则一个已切断的 Console 会被后续请求当成"当前设置源"。
        app.state.platform_settings_client = NullPlatformSettingsClient()
        await pools.aclose()
    await dispose_engine()


async def _db_counters_loop() -> None:
    while True:
        publish_db_counters(record_gauge)
        await asyncio.sleep(1.0)


def _start_background(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    settings: SharedSettings,
    settings_client: PlatformSettingsClient,
    pools: WorkerHttpPools,
    cancel_hints: CancelHintStore,
    wakeup_listener: WakeupListener,
) -> list[asyncio.Task[None]]:
    """装配并启动各**独立**循环：领取、维护（回收）、调度、投递、结果发件互不排队尾。

    所有出站客户端消费装配根注入的分池实例（控制=Console，业务=Gateway/Runtime），
    不各自建池；每个循环是独立的 `asyncio` 任务，带自己的节拍（§3.2、§3.7）。
    """
    worker = WorkerLoop(
        session_factory,
        settings,
        cancel_hints=cancel_hints,
        wakeup=wakeup_listener,
        settings_client=settings_client,
    )
    # 有界任务槽：上限来自每进程启动配置（§3.7 WORKER_EXECUTION_CONCURRENCY），装配根注入。
    app.state.worker_loop = worker
    scheduler = SchedulerLoop(
        session_factory,
        ConsoleResolveClient(
            settings.console_platform_url,
            pools.control,
            service_token=settings.internal_service_token,
        ),
        settings,
        settings_client=settings_client,
    )
    app.state.scheduler_loop = scheduler
    delivery = DeliveryLoop(
        session_factory,
        HttpDeliveryClient(
            settings.im_gateway_url,
            pools.egress,
            service_token=settings.internal_service_token,
        ),
        settings,
        # 产物引用解析：worker 只有不透明的 artifact_id，解析口径在 runtime 一处
        ArtifactResolveClient(
            settings.agent_runtime_url,
            service_token=settings.internal_service_token,
            client=pools.egress,
        ),
        settings_client=settings_client,
        # 网页投递的受信入箱端口：网页"送达"以 Runtime 持久用户收件箱提交为准（TASK-068）。
        webchat_inbox=HttpWebchatInboxClient(
            settings.agent_runtime_url,
            pools.egress,
            service_token=settings.internal_service_token,
        ),
    )
    app.state.delivery_loop = delivery
    results = _result_dispatcher(session_factory, pools.egress, settings)
    app.state.result_dispatcher = results
    loops = (
        (worker.run_forever(), "worker.claim"),
        # 维护循环：回收的独立节拍，不排在领取/执行/投递后面（§3.2）。
        (worker.run_maintenance_forever(), "worker.maintenance"),
        (scheduler.run_forever(), "worker.scheduler"),
        (delivery.run_forever(), "worker.delivery"),
        (results.run_forever(), "worker.results"),
    )
    return [asyncio.create_task(loop, name=name) for loop, name in loops]


def _result_dispatcher(
    factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, settings: SharedSettings
) -> ResultDispatcher:
    return ResultDispatcher(
        factory,
        client,
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        policy=ResultDispatchPolicy.from_settings(settings),
    )


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
app.include_router(runtime_operations_router)
app.include_router(schedules_router)
app.include_router(admin_tasks_router)
app.include_router(admin_results_router)
app.include_router(admin_schedules_router)
