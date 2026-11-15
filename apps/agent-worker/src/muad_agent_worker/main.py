from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from muad_agent_core.sandbox import workspace_layout_from_settings
from muad_api import (
    database_readiness,
    install_api_foundation,
    install_health_probes,
    validate_startup,
)
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings, workspace_policy_from_settings
from muad_common.db_counters import publish_db_counters
from muad_contracts import ScheduleStatus, TaskStatus
from muad_logging import configure_logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .api.admin_results import router as admin_results_router
from .api.admin_schedules import router as admin_schedules_router
from .api.admin_tasks import router as admin_tasks_router
from .api.executions import router as executions_router
from .api.human_interactions import router as human_interactions_router
from .api.runtime_operations import router as runtime_operations_router
from .api.schedule_drafts import router as schedule_drafts_router
from .api.schedules import router as schedules_router
from .api.task_center import router as task_center_router
from .api.tasks import router as tasks_router
from .application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from .application.schedule_retention import ScheduleRetentionLoop
from .delivery.artifact_client import ArtifactResolveClient
from .delivery.client import HttpDeliveryClient, HttpWebchatInboxClient
from .delivery.service import DeliveryLoop
from .infrastructure.cancel_hint import CancelHintStore, create_cancel_hint_store
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.http_clients import WorkerHttpPools, build_pool_client, build_worker_http_pools
from .infrastructure.models.task import TaskExecution, TaskSchedule
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .infrastructure.wakeup_hint import (
    RedisWakeupNotifier,
    WakeupListener,
    create_wakeup_listener,
    create_wakeup_notifier,
)
from .metrics import (
    SCHEDULE_ACTIVE_COUNT_METRIC,
    SCHEDULE_NEEDS_ATTENTION_COUNT_METRIC,
    TASK_WAITING_EXTERNAL_COUNT_METRIC,
    TASK_WAITING_HUMAN_COUNT_METRIC,
    install_worker_metrics,
    record_gauge,
    record_loop_tick,
)
from .results.dispatcher import ResultDispatcher, ResultDispatchPolicy
from .scheduler.client import ConsoleResolveClient
from .scheduler.service import SchedulerLoop
from .worker.human_timeouts import HumanTimeoutLoop, HumanTimeoutSweeper
from .worker.service import WorkerLoop
from .worker.supervision import LoopSupervisor
from .worker.workspace_maintenance import WorkspaceMaintenanceLoop

SERVICE_NAME = "muad-agent-worker"

configure_logging(SERVICE_NAME)
logger = logging.getLogger("muad_agent_worker.main")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    # 启动快照：内部服务门控等热路径只读这一份，不逐请求重读 env/.env。
    app.state.settings = settings
    app.state.workspace_policy = workspace_policy_from_settings(settings)
    await validate_startup(
        settings,
        get_engine(),
        None,
        migrations_dir=settings.migrations_dir,
    )
    session_factory = get_session_factory()
    _seeds = [
        item.strip()
        for item in (settings.redis_cluster_seeds or "").split(",")
        if item.strip()
    ]
    wakeup_notifier = await create_wakeup_notifier(
        settings.redis_url, mode=settings.redis_mode, seeds=_seeds
    )
    app.state.wakeup_notifier = wakeup_notifier
    wakeup_listener = await create_wakeup_listener(
        settings.redis_url, mode=settings.redis_mode, seeds=_seeds
    )
    cancel_hints = await create_cancel_hint_store(
        settings.redis_url, mode=settings.redis_mode, seeds=_seeds
    )
    app.state.cancel_hints = cancel_hints
    # Redis 只是低延迟 hint：不可用时降级为 PG 扫描，不阻塞就绪；仅作为诊断详情上报。
    app.state.wakeup_mode = "redis" if isinstance(wakeup_notifier, RedisWakeupNotifier) else "disabled"
    # 出站分池：预算唯一来源是启动设置（§3.7），装配根建、装配根关，调用方只借用（TASK-095）。
    pools = build_worker_http_pools(settings)
    app.state.http_pools = pools
    # Agentic 的 MCP 出站：与业务同预算、独立客户端（MCP 的 Accept/鉴权头逐会话设置，
    # 不能与其它调用共用默认头）；装配根拥有并关闭。
    agentic_mcp = build_pool_client(settings, "egress")
    app.state.agentic_mcp = agentic_mcp
    # 平台设置快照：任务创建/扇出与投递尝试两个边界各取一次（TASK-006），走控制池。
    settings_client = ConsolePlatformSettingsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    app.state.platform_settings_client = settings_client
    background: list[asyncio.Task[None]] = []
    supervisor: LoopSupervisor | None = None
    try:
        supervisor = _start_background(
            app,
            session_factory,
            settings,
            settings_client,
            pools,
            cancel_hints,
            wakeup_listener,
            agentic_mcp,
        )
        supervisor.start()
        background.append(asyncio.create_task(_db_counters_loop(), name="worker.db_counters"))
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task
        if supervisor is not None:
            await supervisor.aclose()
        await wakeup_notifier.aclose()
        await wakeup_listener.aclose()
        await cancel_hints.aclose()
        await settings_client.aclose()
        # 退出后不留真实 Setting 源：同进程再次进入（测试/多次 lifespan）回落到空对象，
        # 否则一个已切断的 Console 会被后续请求当成"当前设置源"。
        app.state.platform_settings_client = NullPlatformSettingsClient()
        app.state.agentic_mcp = None
        await agentic_mcp.aclose()
        await pools.aclose()
    await dispose_engine()
    # 测试/同进程重入不继承上一轮启动快照；生产退出后进程即结束。
    app.state.settings = None


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
    agentic_mcp: httpx.AsyncClient,
) -> LoopSupervisor:
    """装配各**独立**循环并交给监督器：领取、维护（回收）、调度、投递、结果发件互不排队尾。

    所有出站客户端消费装配根注入的分池实例（控制=Console，业务=Gateway/Runtime），
    不各自建池；每个循环是独立受监督任务，带自己的节拍（§3.2、§3.7）。
    """
    # 延迟导入：Agentic 装配会拉入 Kernel 与 artifact cache（后者导入即建目录）。
    from .worker.assembly import build_agentic_executor

    agentic = build_agentic_executor(
        settings=settings,
        sessions=session_factory,
        pools=pools,
        mcp_http=agentic_mcp,
        settings_client=settings_client,
    )
    worker = WorkerLoop(
        session_factory,
        settings,
        cancel_hints=cancel_hints,
        wakeup=wakeup_listener,
        settings_client=settings_client,
        agentic=agentic,
        # 结果收件箱的只读面：等待前的就绪检查与独立补扫共用同一装配实例（TASK-020）。
        operations=agentic.operations,
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
    # Human 提问超时：与 deadline sweep 并列的独立节拍，节拍沿用同一设置项（不另设同义键）。
    human_timeouts = HumanTimeoutLoop(
        HumanTimeoutSweeper(session_factory),
        interval_sec=settings.task_deadline_sweep_interval_sec,
    )
    app.state.human_timeout_loop = human_timeouts
    # 调度历史保留清理：独立节拍（与 Runtime 的保留循环同一维护节拍来源），
    # 每轮事务外取一次平台设置，再按租户有界删除（TASK-031）。
    retention = ScheduleRetentionLoop(
        session_factory,
        settings_client,
        tenant_id=settings.default_tenant_id,
        interval_sec=settings.run_reaper_interval_sec,
    )
    app.state.schedule_retention_loop = retention
    workspace_loop = _workspace_maintenance(settings, session_factory)
    app.state.workspace_maintenance = workspace_loop
    supervisor = _supervisor(settings)
    _register_loops(
        supervisor,
        settings,
        worker=worker,
        scheduler=scheduler,
        delivery=delivery,
        results=results,
        human_timeouts=human_timeouts,
        retention=retention,
        workspace=workspace_loop,
        session_factory=session_factory,
    )
    app.state.loop_supervisor = supervisor
    return supervisor


def _supervisor(settings: SharedSettings) -> LoopSupervisor:
    return LoopSupervisor(
        check_interval_sec=settings.worker_supervision_interval_sec,
        restart_backoff_sec=settings.worker_loop_restart_backoff_sec,
        shutdown_timeout_sec=settings.worker_loop_shutdown_timeout_sec,
    )


def _register_loops(
    supervisor: LoopSupervisor,
    settings: SharedSettings,
    *,
    worker: WorkerLoop,
    scheduler: SchedulerLoop,
    delivery: DeliveryLoop,
    results: ResultDispatcher,
    human_timeouts: HumanTimeoutLoop,
    retention: ScheduleRetentionLoop,
    workspace: WorkspaceMaintenanceLoop | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """登记独立循环；每个循环的停顿判据取它**自己的**节拍，不从别的循环借。"""
    factor = settings.worker_loop_stale_factor

    def add(name: str, body, interval_sec: float) -> None:
        supervisor.add(name, body, stale_after_sec=interval_sec * factor)

    add("worker.claim", worker.run_forever, settings.worker_poll_interval_sec)
    add("worker.maintenance", worker.run_maintenance_forever, settings.run_reaper_interval_sec)
    add("worker.async_sweep", worker.run_async_sweep_forever, settings.worker_poll_interval_sec)
    add("worker.scheduler.fire", scheduler.run_fire_forever, settings.scheduler_poll_interval_sec)
    add(
        "worker.scheduler.deadline",
        scheduler.run_sweep_forever,
        settings.scheduler_poll_interval_sec,
    )
    add("worker.delivery", delivery.run_forever, settings.delivery_poll_interval_sec)
    add("worker.results", results.run_forever, settings.async_tool_dispatch_poll_sec)
    add(
        "worker.human_timeouts",
        human_timeouts.run_forever,
        settings.task_deadline_sweep_interval_sec,
    )
    add(
        "worker.schedule_retention",
        retention.run_forever,
        settings.run_reaper_interval_sec,
    )
    add(
        "worker.state_gauges",
        lambda: _task_state_gauges_loop(
            session_factory,
            settings.default_tenant_id,
            interval_sec=settings.scheduler_poll_interval_sec,
        ),
        settings.scheduler_poll_interval_sec,
    )
    if workspace is not None:
        add(
            "worker.workspace.maintenance",
            workspace.run_forever,
            settings.run_reaper_interval_sec,
        )


async def _task_state_gauges_loop(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, *, interval_sec: float
) -> None:
    """周期发布状态 gauge：只读一次聚合，不随租户/资源数增长；失败可见并继续。"""
    while True:
        try:
            gauges = await _task_state_gauges(factory, tenant_id)
        except Exception:
            logger.exception("task_state_gauges_failed")
            record_loop_tick("worker.state_gauges", "failed")
        else:
            for name, value in gauges.items():
                record_gauge(name, float(value))
            record_loop_tick("worker.state_gauges", "ok")
        await asyncio.sleep(interval_sec)


async def _task_state_gauges(
    factory: async_sessionmaker[AsyncSession], tenant_id: str
) -> dict[str, int]:
    """租户范围内的调度/等待计数；租户是必填谓词（共享库上不做全局计数）。"""
    async with factory() as session:
        schedule_active, needs_attention = (
            await session.execute(
                select(
                    func.count(TaskSchedule.id).filter(
                        TaskSchedule.status == ScheduleStatus.ACTIVE.value
                    ),
                    func.count(TaskSchedule.id).filter(
                        TaskSchedule.status == ScheduleStatus.NEEDS_ATTENTION.value
                    ),
                ).where(
                    TaskSchedule.tenant_id == tenant_id,
                    TaskSchedule.is_deleted.is_(False),
                )
            )
        ).one()
        waiting_human, waiting_external = (
            await session.execute(
                select(
                    func.count(TaskExecution.id).filter(
                        TaskExecution.status == TaskStatus.WAITING_HUMAN.value
                    ),
                    func.count(TaskExecution.id).filter(
                        TaskExecution.status == TaskStatus.WAITING_EXTERNAL.value
                    ),
                ).where(
                    TaskExecution.tenant_id == tenant_id,
                    TaskExecution.is_deleted.is_(False),
                )
            )
        ).one()
    return {
        SCHEDULE_ACTIVE_COUNT_METRIC: int(schedule_active),
        SCHEDULE_NEEDS_ATTENTION_COUNT_METRIC: int(needs_attention),
        TASK_WAITING_HUMAN_COUNT_METRIC: int(waiting_human),
        TASK_WAITING_EXTERNAL_COUNT_METRIC: int(waiting_external),
    }


def _workspace_maintenance(
    settings: SharedSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> WorkspaceMaintenanceLoop | None:
    """workspace 根未挂载时不得启用清理（PRD：PVC 须同时挂 Runtime/Worker）。"""
    layout = workspace_layout_from_settings(settings)
    if not layout.root.is_dir():
        logger.warning(
            "workspace_cleanup_disabled_root_missing",
            extra={"workspace_root": str(layout.root)},
        )
        return None
    return WorkspaceMaintenanceLoop(
        session_factory,
        layout,
        interval_sec=settings.run_reaper_interval_sec,
    )


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
app.include_router(executions_router)
app.include_router(schedule_drafts_router)
app.include_router(admin_tasks_router)
app.include_router(admin_results_router)
app.include_router(admin_schedules_router)
app.include_router(human_interactions_router)
app.include_router(task_center_router)
