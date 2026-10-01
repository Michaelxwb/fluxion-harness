import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.main import app
from muad_common import SharedSettings
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "task"
TABLES = ("delivery_route", "task_schedule", "task_execution", "task_event", "task_submission")

DELETE_EVENTS = text("DELETE FROM task.task_event WHERE tenant_id = :tenant_id")
DELETE_SUBMISSIONS = text("DELETE FROM task.task_submission WHERE tenant_id = :tenant_id")
DELETE_TASKS = text("DELETE FROM task.task_execution WHERE tenant_id = :tenant_id")
DELETE_SCHEDULES = text("DELETE FROM task.task_schedule WHERE tenant_id = :tenant_id")
DELETE_ROUTES = text("DELETE FROM task.delivery_route WHERE tenant_id = :tenant_id")


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


@pytest.fixture(autouse=True)
async def sweep_stale_test_rows(database_guard: None) -> None:
    """claim 是全局的：清掉崩溃测试残留的到期 Schedule 与可 claim Task，避免跨测试串扰。"""
    stale_tasks = (
        "tenant_id LIKE 'test-%' AND status IN ('QUEUED','WAITING') AND not_before <= now()"
    )
    stale_deliveries = (
        "tenant_id LIKE 'test-%' AND delivery_mode = 'FINAL_ONLY' "
        "AND delivery_status IN ('PENDING','FAILED') "
        "AND status IN ('COMPLETED','FAILED','CANCELLED')"
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        # 到期 Schedule 的清理同样必须先删引用行，否则被 task_execution/task_submission
        # 引用时整条清扫 FK 失败 → 残留越积越多（外键方向：task_event→task_execution→
        # task_schedule，task_submission→两者）。
        due_schedules = (
            "SELECT id FROM task.task_schedule "
            "WHERE tenant_id LIKE 'test-%' AND next_fire_at <= now()"
        )
        for statement in (
            "DELETE FROM task.task_event WHERE task_id IN "
            f"(SELECT id FROM task.task_execution WHERE schedule_id IN ({due_schedules}))",
            f"DELETE FROM task.task_submission WHERE schedule_id IN ({due_schedules})",
            "DELETE FROM task.task_submission WHERE task_id IN "
            f"(SELECT id FROM task.task_execution WHERE schedule_id IN ({due_schedules}))",
            f"DELETE FROM task.task_execution WHERE schedule_id IN ({due_schedules})",
            "DELETE FROM task.task_schedule "
            "WHERE tenant_id LIKE 'test-%' AND next_fire_at <= now()",
        ):
            await session.execute(text(statement))
        for predicate in (stale_tasks, stale_deliveries):
            # 先删引用行，避免 task_event/task_submission 的外键阻塞清理。
            # ⚠️ 还必须连同**子行**一起删：`task_execution.parent_id` 是**自引用外键**，而子行
            # 的 delivery_mode/status 未必匹配谓词（实测子行为 `delivery_mode='NONE'`）——只删
            # 父行会被 FK 挡下，整个夹具 setup 失败，一次中断就让后续**全部**用例 ERROR
            # （2026-10-01 实测：234 个用例在 setup 全挂）。按「子行 → 引用行 → 父行」顺序清。
            targets = f"SELECT id FROM task.task_execution WHERE {predicate}"
            victims = (
                f"SELECT id FROM ({targets}) AS target UNION "
                f"SELECT child.id FROM task.task_execution AS child "
                f"WHERE child.parent_id IN ({targets})"
            )
            for statement in (
                f"DELETE FROM task.task_event WHERE task_id IN ({victims})",
                f"DELETE FROM task.task_submission WHERE task_id IN ({victims})",
                f"DELETE FROM task.task_execution WHERE parent_id IN ({targets})",
                f"DELETE FROM task.task_execution WHERE {predicate}",
            ):
                await session.execute(text(statement))
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
            for statement in (
                DELETE_EVENTS,
                DELETE_SUBMISSIONS,
                DELETE_TASKS,
                DELETE_SCHEDULES,
                DELETE_ROUTES,
            ):
                await session.execute(statement, {"tenant_id": tenant_id})
            await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
