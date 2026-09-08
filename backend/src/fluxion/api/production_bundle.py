"""生产装配 composition root（Phase 6 TASK-006 / FEAT-P6-06）。

phase5 review 指出生产 provider「全仓 grep 无构造点，仅测试接线」——本模块是
生产 app 的唯一装配点（composition root，依赖方向规则 9/14）：

- Registry：``PostgreSQLRegistryStore``（PG DSN，非 PG 直接 fail-fast）；
- Secret：``PostgresEncryptedSecretStore``（PG 密文，替换内存 LocalEncryptedSecretStore）；
- Trace / Approval / EvalRun：PG durable adapter（P0-5「显式 production adapter」）；
- Artifact：``S3CompatibleArtifactStore``（S3/MinIO endpoint 配置，规则 18）；
- Operations：``OperationsApplicationService(sysdb_dsn)``（DBOS sysdb 只读）；
- Release Gate：``release_gate_enforced=True``（phase5 P1-7：无 gate 参数 publish
  fail-closed 在生产生效）；
- 守卫：``verify_production_assembly``（InMemory 唯一实现 → 启动 fail-fast）。

与 ``dev_bundle`` 共用 ApiDispatcher/路由拓扑（Console/Channel/Eval/Workspace/
静态前端），但 provider 全部走生产实现；不 seed 环境凭据（Secret 经 API 管理）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from starlette.applications import Starlette
from starlette.datastructures import URL
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.api.dev_bundle import ApiDispatcher
from fluxion.api.eval import create_app as create_eval_app
from fluxion.api.runtime import create_app as create_runtime_api_app
from fluxion.api.workspace import create_app as create_workspace_app
from fluxion.plugins.artifact.s3 import S3CompatibleArtifactStore
from fluxion.plugins.secret.postgres import PostgresEncryptedSecretStore
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.repositories import (
    PostgresApprovalStore,
    PostgresEvalRunStore,
    PostgresTraceStore,
)
from fluxion.runtime.secrets import CredentialResolver
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.eval_app import (
    EvaluationApplicationService,
    RuleBasedEvalExecutor,
)
from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.operations_app import OperationsApplicationService
from fluxion.services.production_profile import (
    ProductionProfileError,
    verify_production_assembly,
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


@dataclass(frozen=True, slots=True)
class ProductionS3Config:
    """S3/MinIO artifact 配置（规则 18：外部依赖显式配置）。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"


@dataclass
class ProductionAssembly:
    """生产装配持有者（app.state.assembly；测试与运维经此触达真实 provider）。

    FEAT-01：API 进程不再装配本地 AgentRuntime——执行面经
    ``runtime_gateway``（HttpRuntimeGateway）调用独立 Runtime Service；
    outbox worker 等执行侧生命周期留在 Runtime 进程。
    """

    store: PostgreSQLRegistryStore
    secret_store: PostgresEncryptedSecretStore
    trace_store: PostgresTraceStore
    approval_store: PostgresApprovalStore
    eval_run_store: PostgresEvalRunStore
    artifact_store: S3CompatibleArtifactStore | None
    operations: OperationsApplicationService
    console: ConsoleApplicationService
    runtime_gateway: HttpRuntimeGateway

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.secret_store.initialize()
        await self.trace_store.initialize()
        await self.approval_store.initialize()
        await self.eval_run_store.initialize()
        if self.artifact_store is not None:
            await self.artifact_store.initialize()

    async def close(self) -> None:
        if self.artifact_store is not None:
            await self.artifact_store.close()
        await self.operations.close()
        await self.runtime_gateway.aclose()
        await self.store.close()


def _runtime_service_url(explicit: str | None = None) -> str:
    """解析独立 Runtime Service 基址（FEAT-01）。

    Helm 经 ``FLUXION_RUNTIME_SERVICE_URL`` 注入
    ``http://<fullname>-runtime:8000``；非 Helm 远程模式必须显式配置，
    缺失 fail-fast，不静默回退本地执行。
    """
    url = (explicit or os.environ.get("FLUXION_RUNTIME_SERVICE_URL", "")).strip().rstrip("/")
    if not url:
        raise ProductionProfileError(
            "FLUXION_RUNTIME_SERVICE_URL 未设置（production 远程执行必填，"
            "形如 http://<fullname>-runtime:8000）；不回退本地执行"
        )
    return url


def create_production_bundle_app(
    *,
    registry_dsn: str,
    master_key: bytes,
    console_dist: Path,
    chat_dist: Path,
    sysdb_dsn: str | None = None,
    s3_config: ProductionS3Config | None = None,
    runtime_service_url: str | None = None,
) -> Starlette:
    """生产 bundle 装配入口（composition root）。

    - registry_dsn 必须 PostgreSQL（非 PG → ProductionProfileError，不静默降级）；
    - 执行面经 HttpRuntimeGateway 调用独立 Runtime Service（FEAT-01），
      ``runtime_service_url`` 缺省读 ``FLUXION_RUNTIME_SERVICE_URL``，
      缺失 fail-fast，不回退本地/dev 执行；
    - 装配后经 ``verify_production_assembly`` 守卫（InMemory 唯一实现 fail-fast）。
    """
    if not registry_dsn.startswith("postgresql"):
        raise ProductionProfileError(
            "production 装配要求 PostgreSQL DSN（FLUXION_DATABASE_URL），"
            f"收到: {registry_dsn!r}"
        )

    store = PostgreSQLRegistryStore(registry_dsn)
    engine = store.engine
    secret_store = PostgresEncryptedSecretStore(engine=engine, master_key=master_key)
    trace_store = PostgresTraceStore(engine=engine)
    approval_store = PostgresApprovalStore(engine=engine)
    eval_run_store = PostgresEvalRunStore(engine=engine)
    artifact_store = (
        S3CompatibleArtifactStore(
            endpoint=s3_config.endpoint,
            access_key=s3_config.access_key,
            secret_key=s3_config.secret_key,
            bucket=s3_config.bucket,
            engine=engine,
            region=s3_config.region,
        )
        if s3_config is not None
        else None
    )
    # P0-5 守卫：InMemory 唯一实现 → 启动 fail-fast（明确错误，不静默降级）
    verify_production_assembly(
        secret_store=secret_store,
        trace_store=trace_store,
        approval_store=approval_store,
        eval_run_store=eval_run_store,
    )

    credential_resolver = CredentialResolver(secret_store)
    # FEAT-01：执行面拆分——API 进程不创建本地 RuntimeApplicationService；
    # Channel 与 Studio test-run 均经 Gateway 调用独立 Runtime Service。
    gateway = HttpRuntimeGateway(base_url=_runtime_service_url(runtime_service_url))
    channel = ChannelApplicationService(store, gateway)
    eval_service = EvaluationApplicationService(
        store,
        trace_store,
        eval_run_store,
        RuleBasedEvalExecutor(),
        timeout_seconds=10.0,
        catalog=store,
    )
    projection = WorkflowProjectionService(store)
    release_gate = ReleaseGateService(
        eval_service,
        audit_sink=store,
        timeout_seconds=2.0,
    )
    console = ConsoleApplicationService(
        store,
        trace_store=trace_store,
        secret_metadata_store=secret_store,
        approval_store=approval_store,
        # FEAT-01：API 侧无本地执行体，Console 侧用默认实例标识/空插件摘要；
        # 执行 trace 指向远端 RuntimeInstance（见 Gateway 返回）。
        release_gate=release_gate,
        # phase5 P1-7：生产强制 Release Gate——无 gate 参数 publish fail-closed
        release_gate_enforced=True,
        credential_resolver=credential_resolver,
        # golden-path-closure TASK-009：凭据创建/轮换/禁用的明文写入能力
        # （PostgresEncryptedSecretStore 实现完整 SecretStore 协议）。
        secret_store=secret_store,
    )
    operations = OperationsApplicationService(sysdb_dsn)

    assembly = ProductionAssembly(
        store=store,
        secret_store=secret_store,
        trace_store=trace_store,
        approval_store=approval_store,
        eval_run_store=eval_run_store,
        artifact_store=artifact_store,
        operations=operations,
        console=console,
        runtime_gateway=gateway,
    )

    api = ApiDispatcher(
        # FEAT-01：Studio test-run 经 Gateway 走独立 Runtime，不再直连本地 bundle。
        create_console_app(
            console,
            projection_service=projection,
            operations_service=operations,
            runtime_service=gateway,
            user_service=UserDomainService(store),
        ),
        create_channel_app(channel),
        create_eval_app(eval_service),
        create_workspace_app(WorkspaceApplicationService(store)),
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await assembly.initialize()
        try:
            yield
        finally:
            await assembly.close()

    app = Starlette(
        routes=[
            Route("/", _redirect_console),
            Route("/healthz", _health),
            Route("/readyz", _ready),
            Mount("/console", StaticFiles(directory=console_dist, html=True), name="console"),
            Mount("/chat", StaticFiles(directory=chat_dist, html=True), name="chat"),
            Mount("/", app=api),
        ],
        lifespan=lifespan,
    )
    app.state.assembly = assembly
    return app


def create_production_bundle_app_from_env(
    *, console_dist: Path | None = None, chat_dist: Path | None = None
) -> Starlette:
    """env 驱动装配（CLI ``fluxion serve --production`` / 容器 entrypoint 使用）。

    - FLUXION_DATABASE_URL：PG DSN（必填）；
    - FLUXION_SECRET_MASTER_KEY：base64 32B（必填，缺失 → fail-fast）；
    - FLUXION_DBOS_SYSDB_DSN：DBOS sysdb（Operations 端点；缺省不装配 → 空数据）；
    - FLUXION_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET[/REGION]：S3/MinIO
      （缺省不装配 artifact store——S3 是可选生产组件）；
    - FLUXION_RUNTIME_SERVICE_URL：独立 Runtime Service 基址（必填，FEAT-01，
      形如 http://<fullname>-runtime:8000；缺失 fail-fast，不回退本地执行）；
    - FLUXION_CONSOLE_DIST / FLUXION_CHAT_DIST：前端产物目录（缺省用仓库布局）。
    """
    import base64

    registry_dsn = os.environ.get("FLUXION_DATABASE_URL", "")
    raw_key = os.environ.get("FLUXION_SECRET_MASTER_KEY", "")
    if not registry_dsn:
        raise ProductionProfileError("FLUXION_DATABASE_URL 未设置（production 必填）")
    if not raw_key:
        raise ProductionProfileError(
            "FLUXION_SECRET_MASTER_KEY 未设置（production 必填，base64 32B）"
        )
    try:
        master_key = base64.b64decode(raw_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProductionProfileError(
            "FLUXION_SECRET_MASTER_KEY 必须是合法 base64"
        ) from exc

    root = Path(__file__).resolve().parents[4]
    default_console = root / "frontend" / "apps" / "console" / "dist"
    default_chat = root / "frontend" / "apps" / "chat" / "dist"

    s3_config: ProductionS3Config | None = None
    endpoint = os.environ.get("FLUXION_S3_ENDPOINT", "")
    bucket = os.environ.get("FLUXION_S3_BUCKET", "")
    if endpoint and bucket:
        s3_config = ProductionS3Config(
            endpoint=endpoint,
            access_key=os.environ.get("FLUXION_S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("FLUXION_S3_SECRET_KEY", ""),
            bucket=bucket,
            region=os.environ.get("FLUXION_S3_REGION", "us-east-1"),
        )

    return create_production_bundle_app(
        registry_dsn=registry_dsn,
        master_key=master_key,
        console_dist=console_dist or default_console,
        chat_dist=chat_dist or default_chat,
        sysdb_dsn=os.environ.get("FLUXION_DBOS_SYSDB_DSN") or None,
        s3_config=s3_config,
    )


def create_runtime_app_from_env() -> Starlette:
    """env 驱动装配 Runtime-only app（CLI ``fluxion serve --runtime`` / FLUXION_ROLE=runtime）。

    三服务拆分（TASK-010 / 规则 14）：Runtime 独立进程只装配 AgentLoop 执行所需的
    store + secret + trace + RuntimeApplicationService，不含 Console/Channel/Eval/
    Workspace/Operations。api（Control Plane）经 /internal/v1/runtime-profiles/* HTTP
    调用本服务。
    """
    import base64

    registry_dsn = os.environ.get("FLUXION_DATABASE_URL", "")
    raw_key = os.environ.get("FLUXION_SECRET_MASTER_KEY", "")
    if not registry_dsn:
        raise ProductionProfileError("FLUXION_DATABASE_URL 未设置（runtime 必填）")
    if not raw_key:
        raise ProductionProfileError("FLUXION_SECRET_MASTER_KEY 未设置（runtime 必填，base64 32B）")
    try:
        master_key = base64.b64decode(raw_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProductionProfileError("FLUXION_SECRET_MASTER_KEY 必须是合法 base64") from exc
    if not registry_dsn.startswith("postgresql"):
        raise ProductionProfileError("runtime 装配要求 PostgreSQL DSN")

    store = PostgreSQLRegistryStore(registry_dsn)
    engine = store.engine
    secret_store = PostgresEncryptedSecretStore(engine=engine, master_key=master_key)
    trace_store = PostgresTraceStore(engine=engine)
    credential_resolver = CredentialResolver(secret_store)
    # FEAT-07：生产远程执行侧装配真实 PersonalMemoryRetriever（共享 PG engine）。
    runtime_service = RuntimeApplicationService.create_dev_bundle(
        store,
        credential_resolver=credential_resolver,
        trace_store=trace_store,
        memory_retriever=build_personal_memory_retriever(engine),
        memory_recall_timeout_ms=memory_recall_timeout_from_env(),
    )
    app = create_runtime_api_app(runtime_service)

    # S-01 实机发现：runtime 角色此前从不 initialize secret_store，keyring 为空，
    # 一切凭据模型调用失败（secret_key_unavailable）。composition root 在 serving
    # loop 内补初始化（A15：跨 loop 持有连接会坏首请求，故必须走 lifespan）。
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan_with_secret_init(app: Starlette):  # type: ignore[no-untyped-def]
        await secret_store.initialize()
        async with inner_lifespan(app):
            yield

    app.router.lifespan_context = _lifespan_with_secret_init
    # 可观测（dev_bundle 同款）：secret_store 挂 state 供运维/测试断言初始化。
    app.state.secret_store = secret_store
    return app


async def _redirect_console(_request: Request) -> RedirectResponse:
    return RedirectResponse(URL("/console/"))


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mode": "production"})


async def _ready(request: Request) -> JSONResponse:
    """就绪探针：Registry 引擎连通性（k8s readinessProbe）。"""
    assembly: ProductionAssembly = request.app.state.assembly
    try:
        async with assembly.store.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - 探针需要兜底一切库错误
        return JSONResponse(
            {"status": "unavailable", "error": str(error)}, status_code=503
        )
    return JSONResponse({"status": "ready", "mode": "production"})


__all__ = [
    "ProductionAssembly",
    "ProductionS3Config",
    "create_production_bundle_app",
    "create_production_bundle_app_from_env",
    "create_runtime_app_from_env",
]
