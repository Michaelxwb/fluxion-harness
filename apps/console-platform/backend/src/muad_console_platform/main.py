import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_logging import configure_logging
from sqlalchemy.exc import SQLAlchemyError

from .api.router import router
from .application.auth_service import AuthService
from .application.platform_adapter_service import build_default_registry
from .infrastructure.db import dispose_engine, get_session_factory

SERVICE_NAME = "muad-console-platform"

configure_logging(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)


async def _warn_if_no_accounts() -> None:
    settings = SharedSettings()
    if not settings.database_url:
        logger.warning("console_account_check_skipped_database_url_missing")
        return
    try:
        async with get_session_factory()() as session:
            has_accounts = await AuthService(
                session,
                tenant_id=settings.default_tenant_id,
            ).has_any_account()
    except SQLAlchemyError:
        logger.exception("console_account_check_failed")
        return
    if not has_accounts:
        logger.warning("console_no_accounts_run_cli_create_admin")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _warn_if_no_accounts()
    yield
    await dispose_engine()


app = FastAPI(title="MUAD Console Platform", version="0.1.0", lifespan=lifespan)
app.state.platform_adapters = build_default_registry(
    tuple(
        key.strip()
        for key in os.getenv("MUAD_EXTRA_PLATFORM_ADAPTERS", "").split(",")
        if key.strip()
    )
)
install_api_foundation(app)
app.include_router(router)
