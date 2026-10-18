import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from muad_api import (
    database_readiness,
    install_api_foundation,
    install_console_security,
    install_health_probes,
    validate_startup,
)
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_logging import configure_logging
from sqlalchemy.exc import SQLAlchemyError

from .api.deps import ConsoleRoleResolver, ConsoleSessionVerifier
from .api.router import router
from .api.session_renewal import SessionRenewalMiddleware
from .application.auth_service import AuthService
from .application.model_probe_service import ProbeCapacityClient
from .application.platform_adapter_service import build_default_registry
from .infrastructure.db import dispose_engine, get_engine, get_session_factory
from .infrastructure.http_clients import (
    ProbeCapacityUnconfigured,
    build_probe_capacity_client,
    build_probe_http_client,
    probe_budget,
)
from .metrics import install_console_metrics
from .spa import install_spa

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


def _install_probe_clients(fastapi_app: FastAPI, settings: SharedSettings) -> None:
    """探针预算与出站池在装配根一次建好（design §3.7）。

    - 执行预算是契约常量、池大小来自 `SharedSettings`：坏预算/冲突在这里**显式失败**，
      请求路径只消费注入的实例（`api/deps.py` 的同一工厂只服务未跑 lifespan 的 ASGI 测试）；
    - 缺服务身份不建"必然被 403 拒绝"的裸客户端：留下指名日志，请求路径给出显式失败。
    """
    budget = probe_budget(settings)
    capacity_client: ProbeCapacityClient | None = None
    missing: tuple[str, ...] = ()
    try:
        capacity_client = build_probe_capacity_client(settings)
    except ProbeCapacityUnconfigured as exc:
        missing = exc.missing
    http_client = build_probe_http_client(settings)
    fastapi_app.state.probe_budget = budget
    fastapi_app.state.probe_http_client = http_client
    fastapi_app.state.probe_capacity_client = capacity_client
    if missing:
        logger.warning("probe_capacity_client_unconfigured missing=%s", ",".join(missing))


async def _close_probe_clients(fastapi_app: FastAPI) -> None:
    """探针出站池随进程关闭（装配根建、装配根关）；未注入的实例不凭空创建。"""
    for name in ("probe_capacity_client", "probe_http_client"):
        client = getattr(fastapi_app.state, name, None)
        if client is not None:
            await client.aclose()


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    settings = SharedSettings()
    try:
        _install_probe_clients(fastapi_app, settings)
        await validate_startup(
            settings,
            get_engine(),
            NfsArtifactStore(settings.artifact_root),
            migrations_dir=settings.migrations_dir,
        )
        await _warn_if_no_accounts()
        yield
    finally:
        # 启动失败也要收尾：装配根建的池不留在进程里（下一次启动会重建，不靠 GC）。
        await _close_probe_clients(fastapi_app)
        sessions_client = getattr(fastapi_app.state, "platform_sessions_client", None)
        if sessions_client is not None:
            await sessions_client.aclose()
        catalog_client = getattr(fastapi_app.state, "mcp_catalog_cache_client", None)
        if catalog_client is not None:
            await catalog_client.aclose()
        fetch_service = getattr(fastapi_app.state, "artifact_fetch_service", None)
        if fetch_service is not None:
            await fetch_service.aclose()
        await dispose_engine()


def create_app(*, static_dir: Path | None = None) -> FastAPI:
    """装配根：`app` 与测试/多实例共用这一段代码；`static_dir` 显式给定时覆盖 `STATIC_DIR`。

    `lifespan` 用的是**传进来的实例**（不是模块级全局），因此这里的每个实例都有各自的
    `app.state`（探针池/适配器/会话缓存），互不串用。
    """
    fastapi_app = FastAPI(title="MUAD Console Platform", version="0.1.0", lifespan=lifespan)
    fastapi_app.state.platform_adapters = build_default_registry(
        tuple(
            key.strip()
            for key in os.getenv("MUAD_EXTRA_PLATFORM_ADAPTERS", "").split(",")
            if key.strip()
        )
    )
    install_api_foundation(fastapi_app)
    install_console_security(fastapi_app, ConsoleSessionVerifier(), ConsoleRoleResolver())
    fastapi_app.add_middleware(SessionRenewalMiddleware)
    install_health_probes(fastapi_app, {"database": database_readiness(get_engine)})
    install_console_metrics(fastapi_app)
    fastapi_app.include_router(router)
    # 前端构建产物由同一进程提供（docs/01 §10）；必须在所有路由/探针之后——catch-all 只兜底。
    # 开发/E2E 下该目录不存在 → 不注册任何路由（dev 用 vite，E2E 用 vite preview）。
    install_spa(fastapi_app, static_dir or Path(os.getenv("STATIC_DIR", "/app/static")))
    return fastapi_app


app = create_app()
