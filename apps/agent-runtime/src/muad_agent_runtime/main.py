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
from .api.runs import router as runs_router
from .api.tool_results import router as tool_results_router
from .application.async_tools.continuation_pump import ContinuationPump
from .application.async_tools.control_dispatcher import ControlDispatcher, ControlDispatchPolicy
from .application.async_tools.runtime import set_control_dispatcher
from .application.async_tools.supervisor import ExecutionSupervisor
from .application.attachments.tool_results import ArtifactResultWriter
from .application.continuation_execution import continuation_executor
from .application.executor import default_executor_factory
from .application.run_deadline import expire_runs
from .application.run_service import reap_abandoned_runs
from .infrastructure.cancel_hint import create_cancel_hint_store
from .infrastructure.console_client import ConsoleCredentialsClient, ConsoleResolveClient
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
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
        await asyncio.sleep(interval_sec)


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
    app.state.execution_supervisor = ExecutionSupervisor()
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
    deadline_task = asyncio.create_task(_deadline_loop(settings.run_reaper_interval_sec))
    control_sender = asyncio.create_task(dispatcher.run_forever())
    reaper = asyncio.create_task(_reaper_loop(settings.run_reaper_interval_sec))
    try:
        yield
    finally:
        continuation_task.cancel()
        with suppress(asyncio.CancelledError):
            await continuation_task
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
app.include_router(tool_results_router)
