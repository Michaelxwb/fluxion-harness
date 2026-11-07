"""S-45：现有 MCP catalog、Memory 写入/召回、模型选择不被 Skill/Workspace 改动干扰。

真实边界：
- MCP：真实 `McpRuntimeAdapter` 把冻结 catalog 注册进真实 `ToolRegistry`，catalog 归一化/hash
  使用真实 `muad_console_platform.infrastructure.mcp_client`（不 mock 注册与归一化）。
- Memory：真实 PostgreSQL `runtime.user_memory*` / `runtime.memory_scope`，经真实
  `MemoryService` 完成写入、版本递增、召回、软删、恢复与 consolidation 队列表约束检查。
- Model：真实 PostgreSQL `control.model_definition` / `control.agent_definition` 列定义，
  证明不存在 `is_default` 或可回退的平台默认模型。

非干扰是**静态钉住**的：`non_interference_baseline.json` 记录需求开始前（main 合并基点）
这些生产文件的 sha256；本任务代码一旦改动 MCP/Memory/Model 源文件，S-45 立即变红。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolAlreadyRegisteredError, ToolEffect, ToolRegistry
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    tool_registry_name,
)
from muad_agent_runtime.application.memory_commands import (
    MemoryCommand,
    MemoryMutation,
    MemoryWriteContext,
)
from muad_agent_runtime.application.memory_job_enqueue import enqueue_completed
from muad_agent_runtime.application.memory_service import MemoryService
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_console_platform.infrastructure.mcp_client import (
    McpToolSpec,
    catalog_hash,
    normalize_tools,
)
from muad_contracts import ResolvedModel
from muad_contracts.memory import FactContent, MemoryCandidate, MemoryCategory
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .non_interference_support import assert_domain_unchanged

pytestmark = pytest.mark.integration

MEMORY_KEY = "s45.color"


@pytest.fixture()
async def database_engine() -> AsyncIterator[AsyncEngine]:
    """验收基座注入的独立空库；不 mock PG。"""
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def _inspect_schema(connection: sa.Connection, tables: tuple[tuple[str, str], ...]) -> dict[str, object]:
    inspector = sa.inspect(connection)
    result: dict[str, object] = {}
    for schema, table in tables:
        result[f"columns:{table}"] = inspector.get_columns(table, schema=schema)
        result[f"unique:{table}"] = inspector.get_unique_constraints(table, schema=schema)
        result[f"checks:{table}"] = inspector.get_check_constraints(table, schema=schema)
    return result


def _mcp_tools() -> list[McpToolSpec]:
    return [
        McpToolSpec(
            name="zeta.write",
            description="z",
            input_schema={"type": "object"},
            effect="WRITE",
        ),
        McpToolSpec(
            name="alpha.read",
            description="a",
            input_schema={"type": "object"},
            effect="READ",
        ),
    ]


def _mcp_catalog() -> list[dict[str, object]]:
    return normalize_tools(_mcp_tools())


@pytest.mark.asyncio
async def test_s45_mcp_catalog_and_tool_registry_behavior_is_unchanged() -> None:
    """A-S-45-1：catalog 排序/hash 稳定，冻结 catalog 仍注册为 `mcp::server::tool`。"""
    assert_domain_unchanged("mcp")

    catalog = _mcp_catalog()
    assert [entry["name"] for entry in catalog] == ["alpha.read", "zeta.write"]
    assert catalog_hash(catalog) == catalog_hash(normalize_tools(list(reversed(_mcp_tools()))))

    server = McpServerDefinition(
        mcp_server_id=uuid.uuid4(),
        key="s45-server",
        endpoint="http://127.0.0.1:1/mcp",
        catalog_revision=7,
        catalog_hash=catalog_hash(catalog),
        tools=[McpToolDefinition(**entry) for entry in catalog],  # type: ignore[arg-type]
    )
    adapter = McpRuntimeAdapter(audit_writer=None)
    registry = ToolRegistry()
    try:
        adapter.register_catalog(
            registry=registry,
            tenant_id="acc-s45",
            run_id=uuid.uuid4(),
            servers=[server],
        )
        names = [definition.name for definition in registry.list()]
        assert names == [
            tool_registry_name("s45-server", "alpha.read"),
            tool_registry_name("s45-server", "zeta.write"),
        ]
        assert registry.get("mcp::s45-server::alpha.read").effect is ToolEffect.READ
        assert registry.get("mcp::s45-server::zeta.write").effect is ToolEffect.WRITE
        with pytest.raises(ToolAlreadyRegisteredError):
            adapter.register_catalog(
                registry=registry,
                tenant_id="acc-s45",
                run_id=uuid.uuid4(),
                servers=[server],
            )
    finally:
        await adapter.aclose()


async def test_s45_memory_write_recall_soft_delete_version_and_consolidation_are_unchanged(
    database_engine: AsyncEngine,
) -> None:
    """A-S-45-2：写入/版本/召回/软删/恢复/余额历史与 consolidation 队列表约束不变。"""
    assert_domain_unchanged("memory")

    factory = async_sessionmaker(database_engine, expire_on_commit=False)
    service = MemoryService(factory)
    ctx = MemoryWriteContext(
        tenant_id=f"acc-s45-{uuid.uuid4()}",
        user_id=uuid.uuid4(),
        actor_type="ADMIN",
        actor_id="s45-admin",
    )

    created = await service.execute(
        ctx, _upsert(value="blue", expected=None)
    )
    assert created.response.status == "APPLIED"
    assert created.response.items[0].version == 1
    assert created.response.items[0].status == "CREATED"

    updated = await service.execute(ctx, _upsert(value="green", expected=1))
    assert updated.response.items[0].version == 2
    assert updated.response.items[0].status == "UPDATED"

    with pytest.raises(AppError) as stale:
        await service.execute(ctx, _upsert(value="red", expected=1))
    assert stale.value.code == ErrorCode.MEMORY_VERSION_CONFLICT

    injected = await service.list_for_injection(ctx.tenant_id, ctx.user_id, limit=10)
    assert [row["memory_key"] for row in injected] == [MEMORY_KEY]
    assert injected[0]["content_json"] == {"value": "green"}
    recalled = await service.search(ctx.tenant_id, ctx.user_id, prefix="s45", limit=10)
    assert [row["memory_key"] for row in recalled] == [MEMORY_KEY]

    deleted = await service.execute(
        ctx,
        MemoryCommand(
            command_id=uuid.uuid4().hex,
            endpoint="memory.admin",
            mutations=[
                MemoryMutation(
                    action="DELETE", memory_key=MEMORY_KEY, expected_version=2
                )
            ],
        ),
    )
    assert deleted.response.items[0].status == "DELETED"
    assert deleted.response.items[0].version == 3
    assert await service.list_entries(ctx.tenant_id, ctx.user_id) == []
    assert await service.search(ctx.tenant_id, ctx.user_id, prefix="s45", limit=10) == []

    with pytest.raises(AppError) as tombstone:
        await service.execute(ctx, _upsert(value="green", expected=3))
    assert tombstone.value.code == ErrorCode.MEMORY_CONFIRMATION_INVALID

    restored = await service.execute(ctx, _upsert(value="green", expected=3, restore=True))
    assert restored.response.items[0].status == "RESTORED"
    assert restored.response.items[0].version == 4
    assert [row["memory_key"] for row in await service.list_entries(ctx.tenant_id, ctx.user_id)] == [
        MEMORY_KEY
    ]

    from muad_agent_runtime.infrastructure.models.runtime import UserMemoryHistory

    async with factory() as session:
        history = list(
            (
                await session.scalars(
                    sa.select(UserMemoryHistory)
                    .where(
                        UserMemoryHistory.tenant_id == ctx.tenant_id,
                        UserMemoryHistory.user_id == ctx.user_id,
                        UserMemoryHistory.memory_key == MEMORY_KEY,
                    )
                    .order_by(UserMemoryHistory.version)
                )
            ).all()
        )
        assert [row.version for row in history] == [1, 2, 3, 4]
        assert [row.change_reason for row in history] == [
            "CREATE",
            "OVERWRITE",
            "ADMIN_DELETE",
            "RESTORE",
        ]
        # consolidation 队列表仍是唯一 job + 合法状态：只有 COMPLETED Run 才可能入队。
        assert await enqueue_completed(
            session,
            SimpleNamespace(tenant_id=ctx.tenant_id, id=uuid.uuid4(), status="RUNNING"),  # type: ignore[arg-type]
            through_seq=1,
        ) is False

    async with database_engine.connect() as connection:
        schema = await connection.run_sync(
            _inspect_schema, (("runtime", "memory_consolidation_job"),)
        )
    unique = schema["unique:memory_consolidation_job"]
    assert isinstance(unique, list)
    assert any(tuple(item["column_names"]) == ("tenant_id", "run_id") for item in unique)
    checks = schema["checks:memory_consolidation_job"]
    assert isinstance(checks, list)
    assert any("status" in (item.get("sqltext") or "") for item in checks)


async def test_s45_agent_model_selection_has_no_platform_default(
    database_engine: AsyncEngine,
) -> None:
    """A-S-45-3：模型定义无 `is_default`，Agent 必须显式保存非空 `model_id`。"""
    assert_domain_unchanged("model")
    assert ResolvedModel.model_fields["model_id"].is_required()

    async with database_engine.connect() as connection:
        schema = await connection.run_sync(
            _inspect_schema,
            (("control", "model_definition"), ("control", "agent_definition")),
        )
    model_columns = schema["columns:model_definition"]
    assert isinstance(model_columns, list)
    assert "is_default" not in {column["name"] for column in model_columns}
    agent_columns = schema["columns:agent_definition"]
    assert isinstance(agent_columns, list)
    model_id = next(column for column in agent_columns if column["name"] == "model_id")
    assert model_id["nullable"] is False
    assert model_id.get("default") is None


def _upsert(*, value: str, expected: int | None, restore: bool = False) -> MemoryCommand:
    return MemoryCommand(
        command_id=uuid.uuid4().hex,
        endpoint="memory.admin",
        mutations=[
            MemoryMutation(
                action="UPSERT",
                candidate=MemoryCandidate(
                    memory_key=MEMORY_KEY,
                    category=MemoryCategory.PREFERENCE,
                    content=FactContent(value=value),
                ),
                expected_version=expected,
                restore=restore,
            )
        ],
    )
