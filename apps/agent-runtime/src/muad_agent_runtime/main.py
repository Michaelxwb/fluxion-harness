import asyncio
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial

import httpx
from fastapi import FastAPI
from muad_api import (
    database_readiness,
    install_api_foundation,
    install_health_probes,
    validate_startup,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.admin_runs import router as admin_runs_router
from .api.artifacts import router as artifacts_router
from .api.channel_delivery import router as channel_delivery_router
from .api.memory import router as memory_router
from .api.model_capacity import router as model_capacity_router
from .api.runs import router as runs_router
from .api.tool_results import router as tool_results_router
from .api.webchat_history import router as webchat_history_router
from .application.async_tools.continuation_pump import ContinuationPump
from .application.async_tools.control_dispatcher import ControlDispatcher, ControlDispatchPolicy
from .application.async_tools.runtime import set_control_dispatcher
from .application.async_tools.supervisor import ExecutionSupervisor, reclaim_stale_executions
from .application.attachments.tool_results import ArtifactResultWriter
from .application.channel_delivery import TenantChannelDelivery
from .application.channel_delivery_pump import (
    ChannelDeliveryPump,
    DeliveryPumpPolicy,
    GatewayDeliverySender,
)
from .application.continuation_execution import continuation_executor
from .application.executor import default_executor_factory
from .application.memory_consolidation import ConsolidationInvoker
from .application.memory_consolidation_effects import ConsolidationWorker
from .application.memory_supervisor import MemorySupervisor
from .application.run_deadline import expire_runs
from .application.run_service import expire_queued_runs, reap_abandoned_runs
from .infrastructure.cancel_hint import create_cancel_hint_store
from .infrastructure.console_client import ConsoleCredentialsClient, ConsoleResolveClient
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.gateway_delivery_client import GatewayDeliveryClient
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .metrics import install_runtime_metrics

SERVICE_NAME = "muad-agent-runtime"

logger = logging.getLogger(__name__)

configure_logging(SERVICE_NAME)


async def _reaper_loop(interval_sec: float) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await reap_abandoned_runs(get_session_factory())
        except Exception:
            logger.exception("run_reaper_failed")


async def _deadline_loop(interval_sec: float) -> None:
    while True:
        try:
            await expire_runs(get_session_factory())
        except Exception:
            logger.exception("run_deadline_sweep_failed")
        try:
            # 初始排队期限到期：与「领取」竞争同一状态 CAS，超时后不再调用模型（§3.2）。
            await expire_queued_runs(get_session_factory())
        except Exception:
            logger.exception("run_queue_deadline_sweep_failed")
        await asyncio.sleep(interval_sec)


async def _execution_slot_loop(
    interval_sec: float, supervisor: ExecutionSupervisor, instance_id: str
) -> None:
    """执行槽独立监督循环（与领取/回收/控制各自独立，不排在长任务队尾）。

    按 PG 行事实核验每个在飞槽的 Run 是否仍由本实例持有；失去归属（终态、被 Reaper 或别的
    实例接管、租约已过期）的本地片段即使卡死也被取消并释放槽。归属仍在时不误回收。
    """
    while True:
        await asyncio.sleep(interval_sec)
        try:
            reclaimed = await reclaim_stale_executions(
                get_session_factory(), supervisor, instance_id=instance_id
            )
        except Exception:
            logger.exception("execution_slot_supervision_failed")
            continue
        if reclaimed:
            logger.warning("execution_slots_reclaimed", extra={"reclaimed": reclaimed})


def _mount_delivery_pump(
    settings: SharedSettings, platform_settings_client: ConsolePlatformSettingsClient
) -> tuple[ChannelDeliveryPump, GatewayDeliveryClient]:
    """出站投递泵的装配：独立于请求/执行路径（不占执行槽），交付经既有 Gateway 适配器。

    只重试**已持久**结果（TASK-063 的 PENDING 意图），不重新执行任何外部写任务；泵与交付
    客户端都由 lifespan 收尾（先停循环再关在飞投递与客户端）。
    """
    client = GatewayDeliveryClient(
        settings.im_gateway_url,
        service_token=settings.internal_service_token,
        timeout_sec=settings.channel_delivery_timeout_sec,
    )
    pump = ChannelDeliveryPump(
        get_session_factory,
        TenantChannelDelivery(get_session_factory, platform_settings_client),
        GatewayDeliverySender(client),
        DeliveryPumpPolicy.from_settings(settings),
    )
    return pump, client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    artifact_store = NfsArtifactStore(settings.artifact_root)
    await validate_startup(
        settings,
        get_engine(),
        artifact_store,
        migrations_dir=settings.migrations_dir,
    )
    resolve_client = ConsoleResolveClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
    )
    credentials_client = ConsoleCredentialsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
    )
    platform_settings_client = ConsolePlatformSettingsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
    )
    cancel_hints = await create_cancel_hint_store(settings.redis_url)
    app.state.resolve_client = resolve_client
    app.state.credentials_client = credentials_client
    app.state.platform_settings_client = platform_settings_client
    app.state.cancel_hint_store = cancel_hints
    app.state.skill_cache = SkillArtifactCache(artifact_store, settings.skill_cache_root)
    control_client = httpx.AsyncClient(trust_env=False)
    dispatcher = ControlDispatcher(
        get_session_factory(),
        control_client,
        settings.agent_worker_url,
        service_token=settings.internal_service_token,
        policy=ControlDispatchPolicy.from_settings(settings),
    )
    set_control_dispatcher(dispatcher)
    # 有界执行槽：上限是每进程启动配置（§3.7 RUNTIME_EXECUTION_CONCURRENCY），装配根注入。
    app.state.execution_supervisor = ExecutionSupervisor(
        max_concurrency=settings.runtime_execution_concurrency
    )
    logger.info(
        "execution_slots_configured", extra={"slots": settings.runtime_execution_concurrency}
    )
    app.state.instance_id = f"{os.getenv('POD_NAME') or socket.gethostname()}:{uuid.uuid4().hex}"
    resume = continuation_executor(
        resolve_client,
        credentials_client,
        platform_settings_client,
        app.state.execution_supervisor,
        partial(
            default_executor_factory,
            skill_cache=app.state.skill_cache,
            artifact_writer=ArtifactResultWriter(settings.artifact_root),
        ),
        instance_id=app.state.instance_id,
    )
    pump = ContinuationPump(
        get_session_factory(),
        app.state.execution_supervisor,
        resume,
        instance_id=app.state.instance_id,
        lease_sec=settings.run_lease_sec,
    )
    continuation_task = asyncio.create_task(pump.run_forever())
    memory = MemorySupervisor(
        ConsolidationWorker(
            get_session_factory(), ConsolidationInvoker(get_session_factory(), credentials_client)
        ),
        instance_id=app.state.instance_id,
    )
    app.state.memory_supervisor = memory
    memory_task = asyncio.create_task(memory.run_forever())
    deadline_task = asyncio.create_task(_deadline_loop(settings.run_reaper_interval_sec))
    control_sender = asyncio.create_task(dispatcher.run_forever())
    reaper = asyncio.create_task(_reaper_loop(settings.run_reaper_interval_sec))
    # 槽位独立监督循环：与领取/回收/控制各自独立，长任务不阻塞它，它也不阻塞长任务。
    app.state.execution_slot_task = asyncio.create_task(
        _execution_slot_loop(
            settings.run_reaper_interval_sec, app.state.execution_supervisor, app.state.instance_id
        )
    )
    # 出站投递泵：独立于请求/执行路径（不占执行槽、不受请求并发影响），只重试已持久结果；
    # 交付仍经既有 Gateway `/internal/deliveries`（不新增部署单元）。
    app.state.delivery_pump, delivery_client = _mount_delivery_pump(
        settings, platform_settings_client
    )
    app.state.delivery_pump_task = asyncio.create_task(app.state.delivery_pump.run_forever())
    try:
        yield
    finally:
        memory_task.cancel()
        with suppress(asyncio.CancelledError):
            await memory_task
        await memory.close()
        continuation_task.cancel()
        with suppress(asyncio.CancelledError):
            await continuation_task
        app.state.execution_slot_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.execution_slot_task
        app.state.delivery_pump_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.delivery_pump_task
        await app.state.delivery_pump.close()
        await delivery_client.aclose()
        await app.state.execution_supervisor.close()
        deadline_task.cancel()
        with suppress(asyncio.CancelledError):
            await deadline_task
        control_sender.cancel()
        with suppress(asyncio.CancelledError):
            await control_sender
        set_control_dispatcher(None)
        await control_client.aclose()
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await cancel_hints.aclose()
        await credentials_client.aclose()
        await platform_settings_client.aclose()
        await resolve_client.aclose()
        await dispose_engine()


app = FastAPI(title="MUAD Agent Runtime", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
install_runtime_metrics(app)
install_health_probes(
    app,
    {
        "database": database_readiness(get_engine),
        "artifact_storage": lambda: NfsArtifactStore(SharedSettings().artifact_root).root.is_dir(),
    },
)
app.include_router(runs_router)
app.include_router(admin_runs_router)
app.include_router(artifacts_router)
app.include_router(memory_router)
app.include_router(tool_results_router)
app.include_router(model_capacity_router)
app.include_router(webchat_history_router)
app.include_router(channel_delivery_router)
