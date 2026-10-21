import asyncio
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial

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
from .application.channel_retention import retention_policy_from_settings, run_retention_sweep
from .application.continuation_execution import continuation_executor
from .application.executor import ExecutorFactory, default_executor_factory
from .application.memory_consolidation import ConsolidationInvoker
from .application.memory_consolidation_effects import ConsolidationWorker
from .application.memory_supervisor import MemorySupervisor
from .application.run_deadline import expire_runs
from .application.run_service import expire_queued_runs, reap_abandoned_runs
from .application.task_client import WorkerTaskClient
from .infrastructure.cancel_hint import CancelHintStore, create_cancel_hint_store
from .infrastructure.console_client import ConsoleCredentialsClient, ConsoleResolveClient
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.gateway_delivery_client import GatewayDeliveryClient
from .infrastructure.http_clients import RuntimeHttpPools, build_runtime_http_pools
from .infrastructure.platform_settings_client import ConsolePlatformSettingsClient
from .metrics import (
    EXECUTION_SLOT_RECLAIM_METRIC,
    EXECUTION_SLOTS_METRIC,
    RUNTIME_MAINTENANCE_METRIC,
    install_runtime_metrics,
    record_counter,
    record_gauge,
    record_outcome,
)

SERVICE_NAME = "muad-agent-runtime"

#: 装配根启动的**独立**循环任务名（各自成任务、退出即停；B-294 的可观测面）。
CONTINUATION_LOOP_NAME = "runtime.continuation"
MEMORY_LOOP_NAME = "runtime.memory"
DEADLINE_LOOP_NAME = "runtime.deadline"
CONTROL_LOOP_NAME = "runtime.control"
REAPER_LOOP_NAME = "runtime.reaper"
EXECUTION_SLOT_LOOP_NAME = "runtime.execution_slots"
DELIVERY_LOOP_NAME = "runtime.delivery"
RETENTION_LOOP_NAME = "runtime.retention"

logger = logging.getLogger(__name__)

configure_logging(SERVICE_NAME)


async def _reaper_loop(interval_sec: float) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await reap_abandoned_runs(get_session_factory())
        except Exception:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "reaper"})
            logger.exception("run_reaper_failed")
        else:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "reaper"})


async def _deadline_loop(interval_sec: float) -> None:
    while True:
        try:
            await expire_runs(get_session_factory())
        except Exception:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "deadline"})
            logger.exception("run_deadline_sweep_failed")
        try:
            # 初始排队期限到期：与「领取」竞争同一状态 CAS，超时后不再调用模型（§3.2）。
            await expire_queued_runs(get_session_factory())
        except Exception:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "deadline"})
            logger.exception("run_queue_deadline_sweep_failed")
        else:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "deadline"})
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
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "execution_slot"})
            logger.exception("execution_slot_supervision_failed")
            continue
        record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "execution_slot"})
        if reclaimed:
            record_counter(EXECUTION_SLOT_RECLAIM_METRIC, reclaimed)
            logger.warning("execution_slots_reclaimed", extra={"reclaimed": reclaimed})


async def _retention_loop(
    interval_sec: float,
    settings_client: ConsolePlatformSettingsClient,
    *,
    tenant_id: str,
    artifact_root: str,
) -> None:
    """保留清理的独立维护循环（TASK-069 的清理本体，不占请求路径）。

    每轮取一次平台设置边界（**网络读取在清理事务之外**，锁内不联网），再按有界批量清理历史/
    流增量/通知/暂存；租户谓词取本部署租户——不用一个租户的设置去删别的租户的数据。单轮失败
    留痕（`logger.exception`）后继续：清理失败不得静默，也不得杀死循环（§3.7「下一次清理」）。
    """
    while True:
        await asyncio.sleep(interval_sec)
        try:
            snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
            policy = retention_policy_from_settings(snapshot.settings)
        except Exception:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "retention"})
            logger.exception("webchat_retention_settings_failed")
            continue
        try:
            async with get_session_factory()() as session, session.begin():
                report = await run_retention_sweep(
                    session, policy=policy, artifact_root=artifact_root, tenant_id=tenant_id
                )
        except Exception:
            record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "retention"})
            logger.exception("webchat_retention_sweep_failed")
            continue
        record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "retention"})
        logger.info("webchat_retention_loop_tick", extra={"summary": report.summary()})


def _mount_delivery_pump(
    settings: SharedSettings,
    platform_settings_client: ConsolePlatformSettingsClient,
    pools: RuntimeHttpPools,
) -> ChannelDeliveryPump:
    """出站投递泵的装配：独立于请求/执行路径（不占执行槽），交付经既有 Gateway 适配器。

    只重试**已持久**结果（TASK-063 的 PENDING 意图），不重新执行任何外部写任务；泵由 lifespan
    收尾（先停循环再关在飞投递），交付客户端借用装配根的业务池（TASK-094：预算唯一来源）。
    """
    client = GatewayDeliveryClient(
        settings.im_gateway_url,
        service_token=settings.internal_service_token,
        timeout_sec=settings.channel_delivery_timeout_sec,
        client=pools.egress,
    )
    return ChannelDeliveryPump(
        get_session_factory,
        TenantChannelDelivery(get_session_factory, platform_settings_client),
        GatewayDeliverySender(client),
        DeliveryPumpPolicy.from_settings(settings),
    )


def _build_executor_factory(
    settings: SharedSettings, pools: RuntimeHttpPools, skill_cache: SkillArtifactCache
) -> ExecutorFactory:
    """Run 执行器的装配根工厂：模型/MCP/业务池与任务/交付客户端都注入**同一实例**。

    每个 Run 不再各造一个 httpx 连接池（§3.7「模型/MCP/业务分池」）；「谁造谁收」的规矩仍成立
    ——装配根造、装配根关，Run 片段只借用。业务调用另受 Run 冻结的绝对期限约束（TASK-035）。
    """
    return partial(
        default_executor_factory,
        skill_cache=skill_cache,
        artifact_writer=ArtifactResultWriter(settings.artifact_root),
        model_client=pools.model,
        mcp_client=pools.mcp,
        task_client=WorkerTaskClient(
            settings.agent_worker_url,
            service_token=settings.internal_service_token,
            client=pools.egress,
        ),
        delivery_client=GatewayDeliveryClient(
            settings.im_gateway_url,
            service_token=settings.internal_service_token,
            client=pools.egress,
        ),
    )


@dataclass(frozen=True, slots=True)
class _Outbound:
    """装配根拥有的出站资源：分池 + 借用分池的客户端/派发器（谁造谁收）。"""

    pools: RuntimeHttpPools
    cancel_hints: CancelHintStore
    resolve: ConsoleResolveClient
    credentials: ConsoleCredentialsClient
    platform_settings: ConsolePlatformSettingsClient
    dispatcher: ControlDispatcher


async def _build_outbound(app: FastAPI, settings: SharedSettings) -> _Outbound:
    """建分池与出站客户端：池在装配根建/关，调用方只借用（design §3.7 唯一来源）。"""
    pools = build_runtime_http_pools(settings)
    cancel_hints = await create_cancel_hint_store(settings.redis_url)
    resolve = ConsoleResolveClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    credentials = ConsoleCredentialsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    platform_settings = ConsolePlatformSettingsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
        client=pools.control,
    )
    dispatcher = ControlDispatcher(
        get_session_factory(),
        pools.control,
        settings.agent_worker_url,
        service_token=settings.internal_service_token,
        policy=ControlDispatchPolicy.from_settings(settings),
    )
    set_control_dispatcher(dispatcher)
    app.state.http_pools = pools
    app.state.resolve_client = resolve
    app.state.credentials_client = credentials
    app.state.platform_settings_client = platform_settings
    app.state.control_dispatcher = dispatcher
    app.state.cancel_hint_store = cancel_hints
    return _Outbound(pools, cancel_hints, resolve, credentials, platform_settings, dispatcher)


def _start_loops(app: FastAPI, settings: SharedSettings, outbound: _Outbound) -> list[asyncio.Task[None]]:
    """装配并启动各**独立**循环：续跑/记忆/期限/控制/回收/槽监督/投递/保留清理互不排队尾。

    每个循环是独立的 `asyncio` 任务（具名，退出即停）；执行槽上限与各节拍都来自启动设置
    （§3.7），不在这里复制默认值。
    """
    app.state.skill_cache = SkillArtifactCache(
        NfsArtifactStore(settings.artifact_root), settings.skill_cache_root
    )
    app.state.instance_id = f"{os.getenv('POD_NAME') or socket.gethostname()}:{uuid.uuid4().hex}"
    app.state.execution_supervisor = ExecutionSupervisor(
        max_concurrency=settings.runtime_execution_concurrency
    )
    record_gauge(EXECUTION_SLOTS_METRIC, settings.runtime_execution_concurrency)
    logger.info(
        "execution_slots_configured", extra={"slots": settings.runtime_execution_concurrency}
    )
    app.state.executor_factory = _build_executor_factory(
        settings, outbound.pools, app.state.skill_cache
    )
    resume = continuation_executor(
        outbound.resolve,
        outbound.credentials,
        outbound.platform_settings,
        app.state.execution_supervisor,
        app.state.executor_factory,
        instance_id=app.state.instance_id,
    )
    pump = ContinuationPump(
        get_session_factory(),
        app.state.execution_supervisor,
        resume,
        instance_id=app.state.instance_id,
        lease_sec=settings.run_lease_sec,
    )
    invoker = ConsolidationInvoker(
        get_session_factory(), outbound.credentials, client=outbound.pools.model
    )
    app.state.consolidation_invoker = invoker
    memory = MemorySupervisor(
        ConsolidationWorker(get_session_factory(), invoker),
        instance_id=app.state.instance_id,
    )
    app.state.memory_supervisor = memory
    # 出站投递泵：独立于请求/执行路径（不占执行槽、不受请求并发影响），只重试已持久结果。
    app.state.delivery_pump = _mount_delivery_pump(settings, outbound.platform_settings, outbound.pools)
    loops = (
        (pump.run_forever(), CONTINUATION_LOOP_NAME),
        (memory.run_forever(), MEMORY_LOOP_NAME),
        (_deadline_loop(settings.run_reaper_interval_sec), DEADLINE_LOOP_NAME),
        (outbound.dispatcher.run_forever(), CONTROL_LOOP_NAME),
        (_reaper_loop(settings.run_reaper_interval_sec), REAPER_LOOP_NAME),
        # 槽位独立监督循环：与领取/回收/控制各自独立，长任务不阻塞它，它也不阻塞长任务。
        (
            _execution_slot_loop(
                settings.run_reaper_interval_sec,
                app.state.execution_supervisor,
                app.state.instance_id,
            ),
            EXECUTION_SLOT_LOOP_NAME,
        ),
        (app.state.delivery_pump.run_forever(), DELIVERY_LOOP_NAME),
        # 保留清理：节拍沿用既有维护节拍（§3.7「不新增同义键」），一处来源。
        (
            _retention_loop(
                settings.run_reaper_interval_sec,
                outbound.platform_settings,
                tenant_id=settings.default_tenant_id,
                artifact_root=settings.artifact_root,
            ),
            RETENTION_LOOP_NAME,
        ),
    )
    tasks = [asyncio.create_task(loop, name=name) for loop, name in loops]
    tasks.append(asyncio.create_task(_sample_db_pool(), name="runtime.db_pool"))
    named = {task.get_name(): task for task in tasks}
    # 既有观测点保持原名（TASK-032/066 的验收与运维都读它们）。
    app.state.execution_slot_task = named[EXECUTION_SLOT_LOOP_NAME]
    app.state.delivery_pump_task = named[DELIVERY_LOOP_NAME]
    app.state.retention_task = named[RETENTION_LOOP_NAME]
    return tasks


async def _sample_db_pool() -> None:
    """每 2s 发布连接池占用：池耗尽+阻塞式取连接会同时拖死准入与执行，必须可观测。"""
    while True:
        await asyncio.sleep(2.0)
        pool = get_engine().pool
        record_gauge("db_pool_checked_out", pool.checkedout())
        record_gauge("db_pool_size", pool.size())


async def _stop_loops(tasks: list[asyncio.Task[None]]) -> None:
    """退出即停：取消全部独立循环并等待收尾（只吞取消，不吞真实错误）。"""
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


async def _close_resources(app: FastAPI, outbound: _Outbound) -> None:
    """收尾：先停泵/槽监督，再关装配根拥有的资源；分池最后关（借用方先停）。"""
    await app.state.memory_supervisor.close()
    await app.state.delivery_pump.close()
    await app.state.execution_supervisor.close()
    set_control_dispatcher(None)
    await outbound.cancel_hints.aclose()
    # 退出后不留已切断的分池给请求路径：HTTP 依赖回落到按需自建的客户端（deps 的既有兜底）。
    app.state.executor_factory = None
    await outbound.pools.aclose()


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
    outbound = await _build_outbound(app, settings)
    tasks = _start_loops(app, settings, outbound)
    try:
        yield
    finally:
        await _stop_loops(tasks)
        await _close_resources(app, outbound)
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
