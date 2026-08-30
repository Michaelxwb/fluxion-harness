"""生产 worker 装配（Phase 6 TASK-006 k8s 基建）。

`fluxion-workflow-worker --bootstrap fluxion.runtime.workflow_worker_bootstrap:install`
的装配点——worker Deployment（DBOS 执行进程）启动时注入 Registry 读路径与
durable 事实写入：

- definition provider：``store.recall_pinned``（pinned 版本精确回读，拒绝 DRAFT，
  RULE-P3-02）；
- sync psycopg resolver：解释器 subworkflow 在 DBOS 独立 event loop 解析子定义
  （P0-1：async SQLAlchemy engine 不能跨 loop）；
- active reference store/releaser：start acquire / terminal release（TASK-007）；
- ``workflow_run`` 投影 writer（TASK-008；表由 scripts/init_db.py 建）。

capability/agent executor 装配（规则 12 Capability Contract）已就位（TASK-011）：
agent 经 ContextResolver 解析为 pinned Snapshot；deep 执行体（AgentLoop/Tool
Runtime）见后续。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import psycopg

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.workflow_dbos import (
    set_definition_provider,
    set_reference_releaser,
    set_reference_store,
    set_sync_definition_resolver,
)
from fluxion.runtime.workflow_projection import (
    WorkflowRunProjectionWriter,
    release_workflow_active_references,
    set_projection_writer,
)
from fluxion.runtime.workflow_graph import (
    AgentNodeRequest,
    CapabilityNodeRequest,
    set_agent_executor,
    set_capability_executor,
)
from fluxion.services.context_resolver import ContextResolver, ResolverSelector


def install_production_worker_bootstrap(database_url: str) -> None:
    """worker 生产装配入口（`(database_url) -> None`，--bootstrap 加载）。

    review P1-6 启动校验：`--database-url` 是 DBOS sysdb DSN；worker 同时用它
    派生 Registry 读路径。若 operator 把 sysdb 配到独立库（无 registry 表），
    此前静默失败（首个 workflow 解析才报错）——现在启动即校验 resource_
    definitions 表存在，缺失 fail-fast。
    """
    # registry store 用 asyncpg 驱动（SQLAlchemy `postgresql://` 默认 psycopg2，
    # 非本项目声明依赖）；DBOS sysdb 走 psycopg v3。同库不同驱动连接。
    store = PostgreSQLRegistryStore(
        database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    _validate_registry_tables(database_url)

    async def provider(
        tenant_id: str, workflow_id: str, version: str
    ) -> Mapping[str, object]:
        definition = await store.recall_pinned(
            ResourceKind.WORKFLOW, workflow_id, tenant_id=tenant_id, version=version
        )
        return definition.spec_json

    def sync_resolver(
        tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, object]:
        # P0-1：语义同 recall_pinned——pinned 版本精确回读，拒绝 DRAFT
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT spec_json FROM resource_definitions "
                "WHERE tenant_id = %s AND kind = 'workflow' AND resource_id = %s "
                "AND version = %s AND status != 'draft'",
                (tenant_id, workflow_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"definition not found: {workflow_id}@{version}")
        spec: Any = row[0]
        if isinstance(spec, str):
            return dict(json.loads(spec))
        return dict(spec)

    set_definition_provider(provider)
    set_sync_definition_resolver(sync_resolver)
    set_reference_store(store)
    # releaser 走 sync psycopg 路径（解释器在 DBOS 独立 event loop，P0-2 统一）
    set_reference_releaser(
        lambda **kwargs: release_workflow_active_references(database_url, **kwargs)
    )
    set_projection_writer(WorkflowRunProjectionWriter(database_url))

    # TASK-011：capability/agent executor 装配。agent 经 ContextResolver 解析为
    # pinned ExecutionSnapshot；capability 走 Capability Contract（deep 执行体——
    # AgentLoop/Tool Runtime——见后续，装配点已就位不再「未配置即失败」）。
    context_resolver = ContextResolver(store)

    async def agent_executor(request: AgentNodeRequest) -> object:
        selector = ResolverSelector(
            tenant_id=request.tenant_id,
            agent_id=request.agent_ref,
            user_id=request.user_id,
        )
        result = await context_resolver.resolve(selector, session_id=request.run_id)
        return {
            "agent_ref": request.agent_ref,
            "prompt": request.prompt,
            "snapshot_digest": result.snapshot.snapshot_digest,
        }

    async def capability_executor(request: CapabilityNodeRequest) -> object:
        return {
            "prefix": request.prefix,
            "capability_ref": request.capability_ref,
            "input": request.input,
        }

    for prefix in ("skill", "tool", "mcp"):
        set_capability_executor(prefix, capability_executor)
    set_agent_executor(agent_executor)


def _validate_registry_tables(database_url: str) -> None:
    """启动校验：sysdb 库上必须存在 registry 表（防 sysdb/registry 库混淆）。"""
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'resource_definitions'"
        ).fetchone()
    if row is None or int(row[0]) == 0:
        raise RuntimeError(
            "worker 启动校验失败：--database-url 所指库（DBOS sysdb）上不存在 "
            "resource_definitions 表——sysdb 与 Registry 库混淆？"
            "Registry 读路径与 DBOS sysdb 必须同库（或先执行 scripts/init_db.py）"
        )


__all__ = ["install_production_worker_bootstrap"]
