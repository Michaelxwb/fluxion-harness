"""[S-01][RISK-01][RISK-03] 概览聚合真实验收：真实 Console HTTP 一次取全 + 无 N+1 + 不依赖 Redis。

真实边界：真实 Console 子进程（真实 HTTP/登录会话/CSRF）+ 真实 PostgreSQL 逐表回读。

关于 N+1 的判据（RISK-01）：**聚合 SQL 条数不随行数增长**。逐实体查询的条数必然随行数增长，
故"加行后条数不变"是该风险的直接反证；仅断言"条数小"不足以说明问题。计数以进程内真实
会话 + `before_cursor_execute` 监听完成（HTTP 层只做委托，计数对象就是请求处理器的工作）。
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_common import SharedSettings
from muad_console_platform.application.overview_query_service import OverviewQueryService
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.repositories.overview_models import OverviewPayload
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.overview.environment import (
    TENANT,
    OverviewStack,
    clear_engine_caches,
    console_login,
    purge_tenant,
    start_overview_stack,
    stop_overview_stack,
    wait_ready,
)
from tests.acceptance.task_schedule.environment import ServiceProcess, free_port, require, run_db

RESOURCE_KEYS = {
    "enabled_agents",
    "enabled_skills",
    "enabled_mcps",
    "enabled_models",
    "users",
    "active_schedules",
}
CONSOLE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
MAX_AGGREGATE_SQL = 5


@pytest.fixture(scope="module")
def overview_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OverviewStack]:
    root = tmp_path_factory.mktemp("overview-s01")
    stack, processes = start_overview_stack(Path(root))
    try:
        yield stack
    finally:
        stop_overview_stack(processes)
        orphans = [
            process.name
            for process in processes
            if process._process is not None and process._process.poll() is None
        ]
        clear_engine_caches()
        purge_tenant()
        assert orphans == [], f"收尾后仍有未退出进程（孤儿）: {orphans}"


def _scalar(statement: str) -> Any:
    """按租户独立回读（原始 SQL，不经过被测 API）。"""

    async def read(factory: async_sessionmaker[AsyncSession]) -> Any:
        async with factory() as session:
            return await session.scalar(text(statement), {"t": TENANT})

    return run_db(read)


async def _count_statements() -> tuple[int, list[str]]:
    """以 Console 自己的引擎+会话执行一次 `get_overview()`，返回 (SQL 条数, 语句列表)。

    必须用被测服务的引擎：`run_db` 会另起引擎，监听器将捕获不到任何语句（测量失效）。
    """
    captured: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        captured.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with get_session_factory()() as session:
            await OverviewQueryService(session, tenant_id=TENANT).get_overview()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    return len(captured), captured


def _insert_extra_tasks(agent_id: uuid.UUID, actor_id: uuid.UUID, how_many: int) -> None:
    """追加行数：若实现存在 N+1，聚合 SQL 条数会随之增长。"""

    async def insert(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            async with session.begin():
                for index in range(how_many):
                    task_id = uuid.uuid4()
                    session.add(
                        TaskExecution(
                            id=task_id,
                            tenant_id=TENANT,
                            agent_id=agent_id,
                            actor_user_id=actor_id,
                            intent_key="policy_check",
                            skill_id=uuid.uuid4(),
                            skill_artifact_id=uuid.uuid4(),
                            trigger_type="IMMEDIATE",
                            execution_mode="ASYNC",
                            task_type="SKILL",
                            status="RUNNING",
                            input_json={},
                            execution_snapshot_schema_version=1,
                            execution_snapshot_json={"schema_version": 1},
                            snapshot_hash="sha256:" + "e" * 64,
                            idempotency_key=f"overview-extra-{index}-{task_id}",
                            priority=100,
                            attempt=0,
                            max_attempts=3,
                            delivery_mode="NONE",
                            delivery_status="NONE",
                            delivery_key=f"task:{task_id}:final",
                        )
                    )

    run_db(insert)


async def _http_overview(stack: OverviewStack, days: int = 7) -> OverviewPayload:
    client = await console_login(stack.console_url)
    try:
        response = await client.get("/api/v1/overview", params={"days": days})
    finally:
        await client.aclose()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    return OverviewPayload.model_validate(body["data"])


async def test_s01_one_request_returns_all_blocks_matching_independent_reads(
    overview_stack: OverviewStack,
) -> None:
    """[S-01] A single real HTTP request returns every section from tenant Owner tables."""
    data = await _http_overview(overview_stack)
    assert set(data.resources.model_dump()) == RESOURCE_KEYS
    assert (
        data.resources.enabled_agents
        == _scalar(
            "SELECT count(*) FROM control.agent_definition "
            "WHERE tenant_id = :t AND enabled AND NOT is_deleted"
        )
        == overview_stack.seed.enabled_agents
    )
    assert data.resources.enabled_skills == _scalar(
        "SELECT count(*) FROM control.skill WHERE tenant_id = :t AND enabled AND NOT is_deleted"
    )
    assert (
        data.resources.active_schedules
        == _scalar(
            "SELECT count(*) FROM task.task_schedule WHERE tenant_id = :t AND NOT is_deleted "
            "AND status = 'ACTIVE'"
        )
        == overview_stack.seed.active_schedules
    )
    active_tasks = data.activity.queued_tasks + data.activity.running_tasks + data.activity.waiting_tasks
    assert active_tasks == _scalar(
        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t AND NOT is_deleted "
        "AND parent_id IS NULL AND status IN ('QUEUED','RUNNING','WAITING')"
    )
    assert data.kpis.run_count == 0 and data.kpis.run_success_rate is None
    assert data.kpis.total_tokens == 0 and data.kpis.token_coverage is None
    assert data.agent_ranking == []
    assert len(data.trend.runs) == len(data.trend.tasks) == len(data.trend.tokens) == 7
    assert CONSOLE_TIME.fullmatch(data.generated_at)


async def test_risk01_aggregate_sql_count_is_bounded_and_row_independent(
    overview_stack: OverviewStack,
) -> None:
    """[RISK-01] 聚合 SQL 条数 ≤5，且**追加行数后不增长**（N+1 的直接反证）。"""
    baseline_count, baseline_sql = await _count_statements()
    assert baseline_count > 0, "未捕获到任何 SQL，测量本身失效"
    assert baseline_count <= MAX_AGGREGATE_SQL, f"单次聚合用了 {baseline_count} 条 SQL"

    extra = 12
    agent_id = uuid.UUID(str(_scalar("SELECT id FROM control.agent_definition WHERE tenant_id = :t LIMIT 1")))
    actor_id = uuid.UUID(str(_scalar("SELECT id FROM control.platform_user WHERE tenant_id = :t LIMIT 1")))
    _insert_extra_tasks(agent_id, actor_id, extra)

    grown_count, _ = await _count_statements()
    assert grown_count == baseline_count, (
        f"追加 {extra} 行后聚合 SQL 由 {baseline_count} 增至 {grown_count} —— 存在 N+1"
    )

    # Read authoritative runtime/control/task facts; do not read snapshots or Redis.
    joined = " ".join(baseline_sql).lower()
    for table in (
        "control.agent_definition",
        "control.skill",
        "task.task_execution",
        "task.task_schedule",
        "runtime.run_record",
        "runtime.model_invocation_audit",
    ):
        assert table in joined, f"聚合未查询 {table}"
    assert "snapshot" not in joined, "聚合读取了快照表（RISK-03：必须请求时计算）"
    assert "redis" not in joined


async def test_risk03_overview_serves_without_reachable_redis(
    overview_stack: OverviewStack,
) -> None:
    """[RISK-03] Redis 不可达时概览仍可用：另起一个指向死端口的 Console 进程请求同一端点。

    若实现把 KPI 缓存成 Redis 快照或把 Redis 当必经依赖，该进程要么起不来、要么降级/伪造数据。
    """
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    log_path = overview_stack.artifact_root.parent / "logs" / "console-no-redis.log"
    process = ServiceProcess(
        name="console-no-redis",
        module="muad_console_platform.main",
        port=free_port(),
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "REDIS_URL": "redis://127.0.0.1:1/0",  # 死端口：真实不可达
            "ARTIFACT_ROOT": str(overview_stack.artifact_root),
            "DEFAULT_TENANT_ID": TENANT,
        },
        log_path=log_path,
    )
    process.start()
    try:
        await wait_ready(f"{process.url}/healthz")
        client = await console_login(process.url)
        try:
            response = await client.get("/api/v1/overview")
        finally:
            await client.aclose()
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == "0"
        # 与正常栈同一租户：数值必须一致（证明未因 Redis 缺失而伪造或降级）
        seed = overview_stack.seed
        assert body["data"]["resources"]["enabled_agents"] == seed.enabled_agents
        assert body["data"]["resources"]["active_schedules"] == seed.active_schedules
        assert body["data"]["kpis"]["run_count"] == 0
    finally:
        process.stop()
        assert process._process is not None and process._process.poll() is not None, (
            "no-redis 进程未退出（孤儿）"
        )


async def test_metrics_trend_and_status_match_independent_reads(
    overview_stack: OverviewStack,
) -> None:
    """[S-05] Continuous calendar days and root-only period totals match independent SQL."""
    data = await _http_overview(overview_stack)
    days = [point.date for point in data.trend.tasks]
    assert days == sorted(days) and len(days) == 7
    assert (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days == 6

    async def read_window(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            return int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t "
                        "AND NOT is_deleted AND parent_id IS NULL AND create_time <= now() "
                        "AND create_time >= ((now() AT TIME ZONE :tz)::date - 6) AT TIME ZONE :tz"
                    ),
                    {"t": TENANT, "tz": data.timezone},
                )
                or 0
            )

    total = run_db(read_window)
    assert sum(point.total for point in data.trend.tasks) == total
    assert sum(data.status.tasks.values()) == total
