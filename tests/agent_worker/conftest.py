import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.main import app
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ScheduleAdmissionMaterials,
    ScheduleGrantMaterials,
    ScheduleModelMaterials,
)
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.async_tool_datastores import async_tool_database, async_tool_datastore  # noqa: F401

SCHEMA = "task"
TABLES = (
    "delivery_route",
    "task_schedule",
    "task_schedule_revision",
    "task_schedule_draft",
    "schedule_fire",
    "task_execution",
    "task_execution_checkpoint",
    "task_human_interaction",
    "task_delivery",
    "task_delivery_attempt",
    "message_outbox",
    "task_event",
    "task_submission",
    "runtime_operation",
    "runtime_result_outbox",
    "workspace",
    "workspace_lease",
)

#: 删除顺序：子行 → 引用行 → 父行。`task_event` 是多态审计表（entity_type/entity_id），
#: 没有物理 FK，仍按租户整体清理，避免审计行跨用例残留。
TENANT_CLEANUP_ORDER = (
    "task_delivery_attempt",
    "task_delivery",
    "message_outbox",
    "task_human_interaction",
    "task_execution_checkpoint",
    "runtime_result_outbox",
    "runtime_operation",
    "task_event",
    "task_submission",
    "task_execution",
    "schedule_fire",
    "task_schedule_draft",
    "task_schedule_revision",
    "task_schedule",
    "delivery_route",
)

DELETE_LEASES = text(
    "DELETE FROM task.workspace_lease WHERE workspace_id IN "
    "(SELECT id FROM task.workspace WHERE tenant_id = :tenant_id)"
)
DELETE_WORKSPACES = text("DELETE FROM task.workspace WHERE tenant_id = :tenant_id")
DELETE_MESSAGE_DELIVERY_ATTEMPTS = text(
    "DELETE FROM runtime.message_delivery_attempt WHERE tenant_id = :tenant_id"
)
DELETE_MESSAGE_DELIVERIES = text(
    "DELETE FROM runtime.message_delivery WHERE tenant_id = :tenant_id"
)
SWEEP_LEASES = text(
    "DELETE FROM task.workspace_lease WHERE workspace_id IN "
    "(SELECT id FROM task.workspace WHERE tenant_id LIKE 'test-%')"
)
SWEEP_WORKSPACES = text("DELETE FROM task.workspace WHERE tenant_id LIKE 'test-%'")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    session_factory: async_sessionmaker[AsyncSession]
    settings: SharedSettings


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table, schema=SCHEMA) for table in TABLES
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


#: 内部业务路由的测试用服务身份（2026-10-06 评审 #1 起 `/internal/tasks` 与
#: `/internal/schedules` 与 Admin 面同一门控）。`.env` 里通常有值，但测试不依赖它：
#: CI 是干净检出，少了这一格所有内部路由用例都会 403。
TEST_INTERNAL_SERVICE_TOKEN = "test-internal-service-token"


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TEST_INTERNAL_SERVICE_TOKEN)
    return TEST_INTERNAL_SERVICE_TOKEN


@pytest.fixture
def schedule_boundaries():
    """默认只读依赖源/设置源；用例需要时可再 monkeypatch 覆盖。"""

    class _Materials:
        async def resolve_schedule_materials(self, tenant_id, payload):
            return ScheduleAdmissionMaterials(
                mode=payload.mode,
                agent=ResolvedAgent(
                    id=payload.agent_id,
                    key="agent",
                    revision=1,
                    instructions="be brief",
                ),
                model=ScheduleModelMaterials(
                    id=uuid.uuid4(),
                    revision=1,
                    model_id="gpt-4o-mini",
                    protocol="OPENAI",
                ),
                grants=ScheduleGrantMaterials(agent_access=True),
                settings_revision=1,
            )

    class _Settings:
        revision = 1

        async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = ""):
            return PlatformSettingsSnapshot(revision=1, settings={})

    had_materials = hasattr(app.state, "schedule_materials_client")
    previous_materials = getattr(app.state, "schedule_materials_client", None)
    had_settings = hasattr(app.state, "platform_settings_client")
    previous_settings = getattr(app.state, "platform_settings_client", None)
    app.state.schedule_materials_client = _Materials()
    app.state.platform_settings_client = _Settings()
    try:
        yield
    finally:
        if had_materials:
            app.state.schedule_materials_client = previous_materials
        else:
            with suppress(AttributeError, KeyError):
                del app.state.schedule_materials_client
        if had_settings:
            app.state.platform_settings_client = previous_settings
        else:
            with suppress(AttributeError, KeyError):
                del app.state.platform_settings_client


@pytest.fixture(autouse=True)
def dev_sandbox_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker 单测显式使用 dev-unsafe：真实子进程，无 bwrap 的本地主机可跑。

    生产装配仍是 `build_sandboxed_skill_executor(settings)`（bwrap fail closed）；
    真实 bwrap 边界由 acceptance 探针覆盖。
    """
    from muad_agent_core.sandbox import DevUnsafeRunner
    from muad_agent_core.skill import SandboxedSkillExecutor
    from muad_agent_worker.worker import executor as module

    def build(settings: object) -> SandboxedSkillExecutor:
        return SandboxedSkillExecutor(runner=DevUnsafeRunner(), dev_unsafe=True)

    monkeypatch.setattr(module, "build_sandboxed_skill_executor", build)


async def _delete_executions(session: AsyncSession, predicate: str) -> None:
    """按谓词清执行行：先删所有引用行，再删子执行，最后删父执行。"""
    targets = f"SELECT id FROM task.task_execution WHERE {predicate}"
    victims = (
        f"SELECT id FROM ({targets}) AS target UNION "
        f"SELECT child.id FROM task.task_execution AS child WHERE child.parent_id IN ({targets})"
    )
    for statement in (
        f"DELETE FROM task.task_delivery_attempt WHERE delivery_id IN "
        f"(SELECT id FROM task.task_delivery WHERE execution_id IN ({victims}))",
        f"DELETE FROM task.task_delivery WHERE execution_id IN ({victims})",
        f"DELETE FROM task.message_outbox WHERE task_id IN ({victims})",
        f"DELETE FROM task.task_human_interaction WHERE execution_id IN ({victims})",
        f"DELETE FROM task.task_execution_checkpoint WHERE execution_id IN ({victims})",
        f"DELETE FROM task.runtime_operation WHERE task_execution_id IN ({victims})",
        f"UPDATE task.task_submission SET task_id = NULL WHERE task_id IN ({victims})",
        f"DELETE FROM task.task_execution WHERE parent_id IN ({targets})",
        f"DELETE FROM task.task_execution WHERE {predicate}",
    ):
        await session.execute(text(statement))


@pytest.fixture(autouse=True)
async def sweep_stale_test_rows(database_guard: None) -> None:
    """claim 是全局的：清掉崩溃测试残留的到期 Schedule 与可 claim Task，避免跨测试串扰。"""
    stale_tasks = (
        "tenant_id LIKE 'test-%' "
        "AND status IN ('QUEUED','WAITING_EXTERNAL','WAITING_HUMAN') AND not_before <= now()"
    )
    #: **租约过期的 RUNNING 也要清**（2026-10-03 补）：`reclaim_expired` 与 claim 一样是
    #: **全局**的（`_requeue_expired` 只按 status/lease/cancel/is_deleted 过滤，**没有租户谓词**），
    #: 所以上一个用例崩溃留下的过期 RUNNING 会被下一个用例的 reclaim 一并收走 ——
    #: 表现为 `assert reclaim_expired(...) == 1` 偶发变成 `== 2`，失败用例每次都不同、
    #: 且单文件跑必过。缺的正是这一格。
    stale_running = (
        "tenant_id LIKE 'test-%' AND status = 'RUNNING' "
        "AND lease_until IS NOT NULL AND lease_until < now()"
    )
    stale_deliveries = (
        "tenant_id LIKE 'test-%' AND status IN ('PENDING','FAILED') "
        "AND execution_id IN (SELECT id FROM task.task_execution WHERE status IN "
        "('COMPLETED','FAILED','CANCELLED'))"
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        # workspace 清理循环按租约/状态全局扫描；先清掉测试残留，避免跨用例串扰。
        for statement in (SWEEP_LEASES, SWEEP_WORKSPACES):
            await session.execute(statement)
        due_schedules = (
            "SELECT id FROM task.task_schedule "
            "WHERE tenant_id LIKE 'test-%' AND next_fire_at <= now()"
        )
        # 到期 Schedule 必须先清引用行，否则被 task_execution/task_submission 引用时整条
        # 清扫 FK 失败 → 残留越积越多。
        await session.execute(
            text(
                "UPDATE task.task_submission SET schedule_id = NULL, schedule_draft_id = NULL "
                f"WHERE schedule_id IN ({due_schedules})"
            )
        )
        await _delete_executions(session, f"schedule_id IN ({due_schedules})")
        # Schedule.current_revision_id 是指向 Revision 的循环 FK：不先摘掉引用就删不掉 Revision。
        await session.execute(
            text(
                "UPDATE task.task_schedule SET current_revision_id = NULL "
                f"WHERE id IN ({due_schedules})"
            )
        )
        for statement in (
            f"DELETE FROM task.schedule_fire WHERE schedule_id IN ({due_schedules})",
            f"DELETE FROM task.task_schedule_draft WHERE base_schedule_id IN ({due_schedules})",
            f"DELETE FROM task.task_schedule_revision WHERE schedule_id IN ({due_schedules})",
            f"DELETE FROM task.task_schedule WHERE id IN ({due_schedules})",
        ):
            await session.execute(text(statement))
        for predicate in (stale_tasks, stale_running):
            await _delete_executions(session, predicate)
        await session.execute(text(f"DELETE FROM task.task_delivery WHERE {stale_deliveries}"))
        await session.commit()


@pytest.fixture
async def tenant(database_guard: None) -> AsyncIterator[TenantContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    session_factory = get_session_factory()
    context = TenantContext(
        tenant_id=tenant_id,
        session_factory=session_factory,
        settings=SharedSettings(),
    )
    try:
        yield context
    finally:
        async with session_factory() as session:
            await session.execute(DELETE_LEASES, {"tenant_id": tenant_id})
            await session.execute(DELETE_WORKSPACES, {"tenant_id": tenant_id})
            # 循环 FK：先摘掉 Schedule → Revision 的引用，再按顺序删。
            await session.execute(
                text(
                    "UPDATE task.task_schedule SET current_revision_id = NULL "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
            # Fire ↔ Execution 也是循环 FK：回填过 execution_id 的 Fire 必须先摘引用，
            # 否则删 Execution 会撞 fk_schedule_fire_execution。
            await session.execute(
                text(
                    "UPDATE task.schedule_fire SET execution_id = NULL "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
            for table in TENANT_CLEANUP_ORDER:
                await session.execute(
                    text(f"DELETE FROM task.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
            # 读面夹具会直接种 runtime.message_delivery；残留会被 dev 投递泵扫到。
            await session.execute(DELETE_MESSAGE_DELIVERY_ATTEMPTS, {"tenant_id": tenant_id})
            await session.execute(DELETE_MESSAGE_DELIVERIES, {"tenant_id": tenant_id})
            await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
