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
from fluxion.api.workspace import create_app as create_workspace_app
from fluxion.config import DevModeSettings
from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.repositories import PostgresTraceStore
from fluxion.runtime.secrets import CredentialResolver, SecretStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.eval_app import (
    EvaluationApplicationService,
    InMemoryEvalRunStore,
    RuleBasedEvalExecutor,
)
from fluxion.services.release_gate import ReleaseGateService
from fluxion.services.runtime_app import (
    RuntimeApplicationService,
    build_personal_memory_retriever,
    memory_recall_timeout_from_env,
)
from fluxion.services.workflow_projection import WorkflowProjectionService
from fluxion.services.workspace_app import WorkspaceApplicationService
from fluxion.users import UserDomainService


class ApiDispatcher:
    def __init__(
        self, console: FastAPI, channel: FastAPI, eval: FastAPI, workspace: FastAPI
    ) -> None:
        self._routes: tuple[tuple[str, ASGIApp], ...] = (
            ("/api/v1/channels/", channel),
            # TASK-004：Console Eval 页三端点（/api/v1/admin/evals*）归 eval 域
            ("/api/v1/admin/evals", eval),
            ("/api/v1/eval/", eval),
            # TASK-014：Chat Workspace（X402-X408 数据源；Bearer Chat Access Token 鉴权）
            ("/api/v1/workspace", workspace),
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
    """dev bundle 装配入口（composition root，ADR-A007 PG-Only）。

    registry_dsn 必须为 PostgreSQL（与生产同库形态，需先用 scripts/init_db.py
    建表）；密钥必须经 FLUXION_SECRET_MASTER_KEY 显式给（与生产同姿势）。
    非 PG DSN 直接 fail-fast。
    """
    if not registry_dsn.startswith("postgresql"):
        raise ValueError(f"dev bundle requires postgresql DSN (got {registry_dsn!r})")
    store = PostgreSQLRegistryStore(registry_dsn)
    # 与生产同形态：Secret 明文落 PG 加密行（AES-256-GCM），重启不丢失。
    secret_store = PostgresEncryptedSecretStore(
        engine=store.engine, master_key=_dev_master_key()
    )
    credential_resolver = CredentialResolver(secret_store)
    # Trace 统一走 PG（与生产同形态）：dev 重启不丢执行记录。
    # InMemoryTraceStore 只留给单测/无 DSN 场景。
    trace_store = PostgresTraceStore(engine=store.engine)
    # FEAT-07：dev 执行入口装配真实 PersonalMemoryRetriever（与生产同形态）。
    runtime = RuntimeApplicationService.create_dev_bundle(
        store,
        credential_resolver=credential_resolver,
        trace_store=trace_store,
        memory_retriever=build_personal_memory_retriever(store.engine),
        memory_recall_timeout_ms=memory_recall_timeout_from_env(),
    )
    channel = ChannelApplicationService(store, runtime)
    eval_service = EvaluationApplicationService(
        store,
        runtime.trace_store,
        InMemoryEvalRunStore(),
        RuleBasedEvalExecutor(),
        timeout_seconds=10.0,
        catalog=store,
    )
    dev_mode = DevModeSettings(enabled=True)
    # P1-13：dev bundle 也接线投影 API（读 PG registry 投影表；无 DBOS
    # engine → execution history 省略）。production Console 由装配方注入带 engine 的
    # projection service。
    projection = WorkflowProjectionService(store)
    # Phase 5 TASK-005：publish 管道挂 Release Gate（dev bundle 默认接线）
    release_gate = ReleaseGateService(
        eval_service,
        audit_sink=store,
        timeout_seconds=2.0,
    )
    console = ConsoleApplicationService(
        store,
        trace_store=trace_store,
        secret_metadata_store=secret_store,
        plugin_summaries=runtime.plugin_summaries,
        service_instance_id=runtime.service_instance_id,
        release_gate=release_gate,
        credential_resolver=credential_resolver,
        # golden-path-closure TASK-009：凭据创建/轮换/禁用的明文写入能力。
        secret_store=secret_store,
    )
    api = ApiDispatcher(
        # TASK-012：test-run 需要 runtime 执行链；dev bundle 与 production 同为
        # 同进程 bundle 装配，Console 单独部署时 register_studio_routes 显式 503。
        create_console_app(
            console,
            dev_mode=dev_mode,
            projection_service=projection,
            runtime_service=runtime,
            user_service=UserDomainService(store),
        ),
        create_channel_app(channel, dev_mode=dev_mode),
        create_eval_app(eval_service, dev_mode=dev_mode),
        # TASK-014：dev bundle 无 DBOS → signal sender 缺省（审批 decide 返回 503
        # 明确失败，读端点正常）。
        create_workspace_app(WorkspaceApplicationService(store), dev_mode=dev_mode),
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await store.initialize()
        await secret_store.initialize()
        await trace_store.initialize()
        await _seed_environment_credentials(secret_store)
        # FEAT-07：同一事件循环内初始化执行侧（含 memory provider 有限预算探测）。
        await runtime.initialize()
        outbox_worker = runtime.build_outbox_worker()
        outbox_worker.start()
        try:
            yield
        finally:
            await outbox_worker.stop()
            await runtime.close()

    app = Starlette(
        routes=[
            Route("/", _redirect_console),
            Route("/healthz", _health),
            Mount("/console", StaticFiles(directory=console_dist, html=True), name="console"),
            Mount("/chat", StaticFiles(directory=chat_dist, html=True), name="chat"),
            Mount("/", app=api),
        ],
        lifespan=lifespan,
    )
    # FEAT-07：运维/测试经此触达执行侧装配（真实 Retriever 断言入口）。
    app.state.runtime_service = runtime
    app.state.secret_store = secret_store
    return app


async def _redirect_console(_request: Request) -> RedirectResponse:
    return RedirectResponse(URL("/console/"))


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mode": "dev"})


def _dev_master_key() -> bytes:
    """dev 密钥来源：只认显式环境变量（ADR-A007，与生产同姿势）。

    未设置或非法时 fail-fast，不回退文件钥匙/随机钥匙（否则密文写一次即
    永久不可解，或重启丢失）。
    """
    encoded = os.environ.get("FLUXION_SECRET_MASTER_KEY")
    if not encoded:
        raise RuntimeError(
            "dev 必须显式设置 FLUXION_SECRET_MASTER_KEY（base64 32B）"
        )
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("FLUXION_SECRET_MASTER_KEY must be valid base64") from exc
    if len(key) != 32:
        raise RuntimeError("FLUXION_SECRET_MASTER_KEY must decode to 32 bytes")
    return key


async def _seed_environment_credentials(store: SecretStore) -> None:
    model_key = os.environ.get("FLUXION_MODEL_API_KEY")
    if model_key:
        await store.put("dev", "model", model_key)
    mcp_token = os.environ.get("FLUXION_MCP_TOKEN")
    if mcp_token:
        await store.put("dev", "mcp", mcp_token)
