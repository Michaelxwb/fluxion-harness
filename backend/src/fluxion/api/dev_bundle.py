from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.datastructures import URL
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.api.eval import create_app as create_eval_app
from fluxion.config import DevModeSettings
from fluxion.registry import SQLiteRegistryStore
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.eval_app import (
    EvaluationApplicationService,
    InMemoryEvalRunStore,
    RuleBasedEvalExecutor,
)
from fluxion.services.runtime_app import RuntimeApplicationService


class ApiDispatcher:
    def __init__(self, console: FastAPI, channel: FastAPI, eval: FastAPI) -> None:
        self._routes: tuple[tuple[str, ASGIApp], ...] = (
            ("/api/v1/channels/", channel),
            ("/api/v1/eval/", eval),
            ("/", console),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        for prefix, target in self._routes:
            if path.startswith(prefix):
                await target(scope, receive, send)
                return


def create_dev_bundle_app(
    *,
    registry_dsn: str,
    console_dist: Path,
    chat_dist: Path,
) -> Starlette:
    store = SQLiteRegistryStore(registry_dsn)
    secret_store = _secret_store()
    credential_resolver = CredentialResolver(secret_store)
    runtime = RuntimeApplicationService.create_dev_bundle(
        store,
        credential_resolver=credential_resolver,
    )
    console = ConsoleApplicationService(
        store,
        trace_store=runtime.trace_store,
        secret_metadata_store=secret_store,
        plugin_summaries=runtime.plugin_summaries,
        service_instance_id=runtime.service_instance_id,
    )
    channel = ChannelApplicationService(store, runtime)
    eval_service = EvaluationApplicationService(
        store,
        runtime.trace_store,
        InMemoryEvalRunStore(),
        RuleBasedEvalExecutor(),
        timeout_seconds=10.0,
    )
    dev_mode = DevModeSettings(enabled=True)
    api = ApiDispatcher(
        create_console_app(console, dev_mode=dev_mode),
        create_channel_app(channel, dev_mode=dev_mode),
        create_eval_app(eval_service, dev_mode=dev_mode),
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await store.initialize()
        await _seed_environment_credentials(secret_store)
        try:
            yield
        finally:
            await runtime.close()

    return Starlette(
        routes=[
            Route("/", _redirect_console),
            Route("/healthz", _health),
            Mount("/console", StaticFiles(directory=console_dist, html=True), name="console"),
            Mount("/chat", StaticFiles(directory=chat_dist, html=True), name="chat"),
            Mount("/", app=api),
        ],
        lifespan=lifespan,
    )


async def _redirect_console(_request: Request) -> RedirectResponse:
    return RedirectResponse(URL("/console/"))


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mode": "dev"})


def _secret_store() -> LocalEncryptedSecretStore:
    encoded = os.environ.get("FLUXION_SECRET_MASTER_KEY")
    if encoded is None:
        return LocalEncryptedSecretStore(master_key=os.urandom(32))
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("FLUXION_SECRET_MASTER_KEY must be valid base64") from exc
    return LocalEncryptedSecretStore(master_key=key)


async def _seed_environment_credentials(store: LocalEncryptedSecretStore) -> None:
    model_key = os.environ.get("FLUXION_MODEL_API_KEY")
    if model_key:
        await store.put("dev", "model", model_key)
    mcp_token = os.environ.get("FLUXION_MCP_TOKEN")
    if mcp_token:
        await store.put("dev", "mcp", mcp_token)
