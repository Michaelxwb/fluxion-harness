import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

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

from .api.runs import router as runs_router
from .application.run_service import reap_abandoned_runs
from .infrastructure.cancel_hint import create_cancel_hint_store
from .infrastructure.console_client import ConsoleCredentialsClient, ConsoleResolveClient
from .infrastructure.db import dispose_engine, get_engine, get_session_factory

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
    resolve_client = ConsoleResolveClient(settings.console_platform_url)
    credentials_client = ConsoleCredentialsClient(
        settings.console_platform_url,
        service_token=settings.internal_service_token,
    )
    cancel_hints = await create_cancel_hint_store(settings.redis_url)
    app.state.resolve_client = resolve_client
    app.state.credentials_client = credentials_client
    app.state.cancel_hint_store = cancel_hints
    app.state.skill_cache = SkillArtifactCache(artifact_store, settings.skill_cache_root)
    reaper = asyncio.create_task(_reaper_loop(settings.run_reaper_interval_sec))
    try:
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await cancel_hints.aclose()
        await credentials_client.aclose()
        await resolve_client.aclose()
        await dispose_engine()


app = FastAPI(title="MUAD Agent Runtime", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
install_health_probes(
    app,
    {
        "database": database_readiness(get_engine),
        "artifact_storage": lambda: NfsArtifactStore(SharedSettings().artifact_root).root.is_dir(),
    },
)
app.include_router(runs_router)
