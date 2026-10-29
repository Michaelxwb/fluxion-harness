from functools import partial
from typing import Annotated, cast

from fastapi import Depends, Request
from muad_api.context import current_tenant_id
from muad_artifact_store import SkillArtifactCache
from muad_common import process_settings
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.async_tools.supervisor import ExecutionSupervisor
from ..application.attachments.tool_results import ArtifactResultWriter
from ..application.executor import ExecutorFactory, default_executor_factory
from ..application.ports import (
    AdmissionMaterialsClient,
    CredentialsClient,
    PlatformSettingsClient,
    ResolveClient,
)
from ..application.run_service import RunService
from ..application.skill_tools import build_default_skill_cache
from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.console_client import ConsoleResolveClient
from ..infrastructure.db import get_session
from ..infrastructure.platform_settings_client import ConsolePlatformSettingsClient


def get_tenant_id() -> str:
    return current_tenant_id() or process_settings().default_tenant_id


def get_instance_id(request: Request) -> str:
    return cast(str, request.app.state.instance_id)


def get_credential_canaries() -> tuple[str, ...]:
    """Live process credentials can never be stored, replayed or echoed (design §3.4.5)."""
    token = process_settings().internal_service_token
    return (token,) if token else ()


def get_resolve_client(request: Request) -> ResolveClient:
    client: ResolveClient | None = getattr(request.app.state, "resolve_client", None)
    if client is None:
        settings = process_settings()
        client = ConsoleResolveClient(
            settings.console_platform_url,
            service_token=settings.internal_service_token,
        )
        request.app.state.resolve_client = client
    return client


def get_platform_settings_client(request: Request) -> PlatformSettingsClient:
    client: PlatformSettingsClient | None = getattr(request.app.state, "platform_settings_client", None)
    if client is None:
        settings = process_settings()
        client = ConsolePlatformSettingsClient(
            settings.console_platform_url,
            service_token=settings.internal_service_token,
        )
        request.app.state.platform_settings_client = client
    return client


def get_skill_cache(request: Request) -> SkillArtifactCache:
    cache: SkillArtifactCache | None = getattr(request.app.state, "skill_cache", None)
    if cache is None:
        cache = build_default_skill_cache()
        request.app.state.skill_cache = cache
    return cache


def get_artifact_writer() -> ArtifactResultWriter:
    return ArtifactResultWriter(process_settings().artifact_root)


def get_executor_factory(
    request: Request,
    skill_cache: Annotated[SkillArtifactCache, Depends(get_skill_cache)],
    artifact_writer: Annotated[ArtifactResultWriter, Depends(get_artifact_writer)],
) -> ExecutorFactory:
    """装配根注入的执行器工厂（模型/MCP/业务分池 + 任务/交付客户端同一实例）。

    未装配（测试/直构）时按需自建：不复制池预算，也不给每次 Run 漏一个连接池。
    """
    factory = getattr(request.app.state, "executor_factory", None)
    if factory is not None:
        return cast(ExecutorFactory, factory)
    return partial(
        default_executor_factory,
        skill_cache=skill_cache,
        artifact_writer=artifact_writer,
    )


def get_credentials_client(request: Request) -> CredentialsClient | None:
    """由 main lifespan 注入；未配置（测试/本地）时使用定义内密钥。"""
    return getattr(request.app.state, "credentials_client", None)


def get_cancel_hint_store(request: Request) -> CancelHintStore:
    store = getattr(request.app.state, "cancel_hint_store", None)
    if store is None:
        return NullCancelHintStore()
    return cast(CancelHintStore, store)


def get_execution_supervisor(request: Request) -> ExecutionSupervisor:
    return cast(ExecutionSupervisor, request.app.state.execution_supervisor)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[str, Depends(get_tenant_id)]
CredentialCanariesDep = Annotated[tuple[str, ...], Depends(get_credential_canaries)]
InstanceIdDep = Annotated[str, Depends(get_instance_id)]
ResolveClientDep = Annotated[ResolveClient, Depends(get_resolve_client)]
SkillCacheDep = Annotated[SkillArtifactCache, Depends(get_skill_cache)]
ExecutorFactoryDep = Annotated[ExecutorFactory, Depends(get_executor_factory)]
CancelHintStoreDep = Annotated[CancelHintStore, Depends(get_cancel_hint_store)]
CredentialsClientDep = Annotated[CredentialsClient | None, Depends(get_credentials_client)]
PlatformSettingsClientDep = Annotated[PlatformSettingsClient, Depends(get_platform_settings_client)]
SupervisorDep = Annotated[ExecutionSupervisor, Depends(get_execution_supervisor)]


def get_run_service(
    session: SessionDep,
    resolve_client: ResolveClientDep,
    instance_id: InstanceIdDep,
    executor_factory: ExecutorFactoryDep,
    cancel_hints: CancelHintStoreDep,
    credentials_client: CredentialsClientDep,
    settings_client: PlatformSettingsClientDep,
    supervisor: SupervisorDep,
) -> RunService:
    return RunService(
        session,
        resolve_client,
        instance_id,
        executor_factory=executor_factory,
        cancel_hints=cancel_hints,
        credentials_client=credentials_client,
        # 合并取数与解析共用同一个 Console 客户端：DI 覆盖 `get_resolve_client` 时（测试或替换
        # 装配）拿到的是同一个覆盖实例，不再从 app.state 另取一份绕过覆盖。
        materials_client=(
            cast(AdmissionMaterialsClient, resolve_client)
            if isinstance(resolve_client, AdmissionMaterialsClient)
            else None
        ),
        settings_client=settings_client,
        supervisor=supervisor,
    )


RunServiceDep = Annotated[RunService, Depends(get_run_service)]
