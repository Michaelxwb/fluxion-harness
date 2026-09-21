import os
import socket
from functools import partial
from typing import Annotated, cast

from fastapi import Depends, Request
from muad_api.context import current_tenant_id
from muad_artifact_store import SkillArtifactCache
from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.artifacts import ArtifactResultWriter
from ..application.executor import ExecutorFactory, default_executor_factory
from ..application.ports import CredentialsClient, ResolveClient
from ..application.run_service import RunService
from ..application.skill_tools import build_default_skill_cache
from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.console_client import ConsoleResolveClient
from ..infrastructure.db import get_session


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def get_instance_id() -> str:
    return os.getenv("POD_NAME") or socket.gethostname()


def get_resolve_client(request: Request) -> ResolveClient:
    client: ResolveClient | None = getattr(request.app.state, "resolve_client", None)
    if client is None:
        client = ConsoleResolveClient(SharedSettings().console_platform_url)
        request.app.state.resolve_client = client
    return client


def get_skill_cache(request: Request) -> SkillArtifactCache:
    cache: SkillArtifactCache | None = getattr(request.app.state, "skill_cache", None)
    if cache is None:
        cache = build_default_skill_cache()
        request.app.state.skill_cache = cache
    return cache


def get_artifact_writer() -> ArtifactResultWriter:
    return ArtifactResultWriter(SharedSettings().artifact_root)


def get_executor_factory(
    skill_cache: Annotated[SkillArtifactCache, Depends(get_skill_cache)],
    artifact_writer: Annotated[ArtifactResultWriter, Depends(get_artifact_writer)],
) -> ExecutorFactory:
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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[str, Depends(get_tenant_id)]
InstanceIdDep = Annotated[str, Depends(get_instance_id)]
ResolveClientDep = Annotated[ResolveClient, Depends(get_resolve_client)]
SkillCacheDep = Annotated[SkillArtifactCache, Depends(get_skill_cache)]
ExecutorFactoryDep = Annotated[ExecutorFactory, Depends(get_executor_factory)]
CancelHintStoreDep = Annotated[CancelHintStore, Depends(get_cancel_hint_store)]
CredentialsClientDep = Annotated[CredentialsClient | None, Depends(get_credentials_client)]


def get_run_service(
    session: SessionDep,
    resolve_client: ResolveClientDep,
    instance_id: InstanceIdDep,
    executor_factory: ExecutorFactoryDep,
    cancel_hints: CancelHintStoreDep,
    credentials_client: CredentialsClientDep,
) -> RunService:
    return RunService(
        session,
        resolve_client,
        instance_id,
        executor_factory=executor_factory,
        cancel_hints=cancel_hints,
        credentials_client=credentials_client,
    )


RunServiceDep = Annotated[RunService, Depends(get_run_service)]
