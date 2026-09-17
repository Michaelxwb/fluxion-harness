import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from muad_api import install_api_foundation
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings
from muad_logging import configure_logging

from .api.health import router as health_router
from .api.runs import router as runs_router
from .application.run_service import reap_abandoned_runs
from .infrastructure.cancel_hint import create_cancel_hint_store
from .infrastructure.console_client import ConsoleResolveClient
from .infrastructure.db import dispose_engine, get_session_factory

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
    resolve_client = ConsoleResolveClient(settings.console_platform_url)
    cancel_hints = await create_cancel_hint_store(settings.redis_url)
    app.state.resolve_client = resolve_client
    app.state.cancel_hint_store = cancel_hints
    app.state.skill_cache = SkillArtifactCache(
        NfsArtifactStore(settings.artifact_root),
        settings.skill_cache_root,
    )
    reaper = asyncio.create_task(_reaper_loop(settings.run_reaper_interval_sec))
    try:
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await cancel_hints.aclose()
        await resolve_client.aclose()
        await dispose_engine()


app = FastAPI(title="MUAD Agent Runtime", version="0.1.0", lifespan=lifespan)
install_api_foundation(app)
app.include_router(health_router)
app.include_router(runs_router)
