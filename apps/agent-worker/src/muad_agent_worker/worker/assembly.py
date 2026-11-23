"""Worker 的运行时装配：workspace store 与 AGENTIC 执行器（客户端都由装配根注入）。

只做构造，不做 IO：进程启动时调用一次，构造出的执行器由全部任务槽共享（与 REMINDER/SKILL
执行器一致）。出站客户端借用装配根的共享池（`WorkerHttpPools`），本模块不另建连接池。
"""

from __future__ import annotations

import httpx
from muad_agent_core.sandbox import WorkspaceQuotaPolicy, WorkspaceStore, workspace_layout_from_settings
from muad_agent_core.skill import build_sandboxed_skill_executor
from muad_common import SharedSettings
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.run import AgenticDependencies
from ..application.agentic.session_context import DbSessionContextReader
from ..application.agentic.snapshot import load_frozen_execution
from ..application.agentic.suspension import AgenticSuspension
from ..application.ports import PlatformSettingsClient
from ..infrastructure.console_credentials import ConsoleCredentialClient
from ..infrastructure.http_clients import WorkerHttpPools
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.task_artifact_client import TaskArtifactClient
from ..infrastructure.task_capacity_client import TaskCapacityClient
from ..infrastructure.task_operation_client import TaskOperationClient
from ..infrastructure.workspace_store import WorkerWorkspaceMetadataStore
from ..metrics import record_workspace_quota_rejected
from .agentic_executor import AgenticTaskExecutor
from .agentic_skills import AgenticSkillTools


def build_workspace_store(
    settings: SharedSettings, sessions: async_sessionmaker[AsyncSession]
) -> WorkspaceStore:
    """workspace 租约/配额存储：任务执行与 Agentic 工具共用同一份配额事实。"""
    layout = workspace_layout_from_settings(settings)
    metadata = WorkerWorkspaceMetadataStore(lambda: sessions)
    return WorkspaceStore(
        layout=layout,
        metadata=metadata,
        quota=WorkspaceQuotaPolicy.from_layout(layout),
        usage=metadata,
        on_quota_rejected=record_workspace_quota_rejected,
    )


def build_agentic_executor(
    *,
    settings: SharedSettings,
    sessions: async_sessionmaker[AsyncSession],
    pools: WorkerHttpPools,
    mcp_http: httpx.AsyncClient,
    settings_client: PlatformSettingsClient | None = None,
) -> AgenticTaskExecutor:
    """AGENTIC 执行器：模型容量/异步操作/产物走 Runtime，凭据解析走 Console。

    `settings_client` 只用于执行开始时读取一次平台默认回复语言；预算、压缩与记忆仍在
    Snapshot 里冻结，不由本路径重新解析。
    """
    # 延迟导入：bootstrap 在导入时创建 artifact/cache 目录，只做装配的浏览器/单测不该有
    # 文件系统副作用（`main` 也因此只在启动装配时才引入本模块）。
    from ..bootstrap.artifacts import skill_artifact_cache

    capacity = TaskCapacityClient(
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        client=pools.egress,
    )
    operations = TaskOperationClient(
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        client=pools.egress,
    )
    artifacts = TaskArtifactClient(
        settings.agent_runtime_url,
        service_token=settings.internal_service_token,
        client=pools.egress,
    )
    credentials = ConsoleCredentialClient(
        settings.console_platform_url,
        pools.control,
        service_token=settings.internal_service_token,
    )
    workspace_store = build_workspace_store(settings, sessions)
    skill_executor = build_sandboxed_skill_executor(settings)
    deps = AgenticDependencies(
        settings=settings,
        sessions=sessions,
        artifact_resolver=skill_artifact_cache,
        skill_executor=skill_executor,
        settings_client=settings_client,
        capacity=capacity,
        credentials=credentials,
        operations=operations,
        artifacts=artifacts,
        mcp_http=mcp_http,
        workspace_store=workspace_store,
        session_context=DbSessionContextReader(sessions),
    )

    def skill_runner(
        task: TaskExecution, suspension: AgenticSuspension, egress: HostEgressExecution | None,
    ) -> AgenticSkillTools:
        frozen = load_frozen_execution(task)
        return AgenticSkillTools(
            task=task,
            resolver=skill_artifact_cache,
            executor=skill_executor,
            settings=settings,
            sessions=sessions,
            suspension=suspension,
            operations=operations,
            workspace_store=workspace_store,
            skills={entry.skill_id: entry for entry in frozen.snapshot.skills},
            egress=egress,
        )

    return AgenticTaskExecutor(deps=deps, skill_runner=skill_runner)


__all__ = ["build_agentic_executor", "build_workspace_store"]
