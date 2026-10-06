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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.overview.environment import (
    ACTOR_NAME,
    AGENT_NAME,
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

KPI_KEYS = {"enabled_agents", "enabled_skills", "active_tasks", "active_schedules"}
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


async def test_s01_one_request_returns_all_blocks_matching_independent_reads(
    overview_stack: OverviewStack,
) -> None:
    """[S-01] 真实 Console 一次请求返回 4 KPI + 两组列表，数值与独立 SQL 回读一致。"""
    seed = overview_stack.seed
    client = await console_login(overview_stack.console_url)
    try:
        response = await client.get("/api/v1/overview")
    finally:
        await client.aclose()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    data = body["data"]
    assert set(data) == {"kpis", "recent_tasks", "next_schedules"}
    assert set(data["kpis"]) == KPI_KEYS

    # 与**独立 SQL 回读**逐项一致（不经被测 API 计算）
    assert data["kpis"]["enabled_agents"] == _scalar(
        "SELECT count(*) FROM control.agent_definition "
        "WHERE tenant_id = :t AND enabled AND NOT is_deleted"
    )
    assert data["kpis"]["enabled_skills"] == _scalar(
        "SELECT count(*) FROM control.skill WHERE tenant_id = :t AND enabled AND NOT is_deleted"
    )
    assert data["kpis"]["active_tasks"] == _scalar(
        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t AND NOT is_deleted "
        "AND status IN ('QUEUED','RUNNING','WAITING')"
    )
    assert data["kpis"]["active_schedules"] == _scalar(
        "SELECT count(*) FROM task.task_schedule WHERE tenant_id = :t AND NOT is_deleted "
        "AND status = 'ACTIVE'"
    )
    assert data["kpis"]["enabled_agents"] == seed.enabled_agents
    assert data["kpis"]["active_schedules"] == seed.active_schedules

    # 两组列表各 ≤5、顺序与口径正确、条目字段齐备
    tasks = data["recent_tasks"]
    assert 0 < len(tasks) <= 5
    assert [item["create_time"] for item in tasks] == sorted(
        (item["create_time"] for item in tasks), reverse=True
    )
    assert tasks[0]["agent_name"] == AGENT_NAME
    assert tasks[0]["actor_user_name"] == ACTOR_NAME
    assert all(CONSOLE_TIME.match(item["create_time"]) for item in tasks)

    schedules = data["next_schedules"]
    assert [item["name"] for item in schedules] == list(seed.next_schedule_names)
    assert [item["next_fire_at"] for item in schedules] == sorted(
        item["next_fire_at"] for item in schedules
    )
    assert all(item["status"] == "ACTIVE" for item in schedules)


async def test_risk01_aggregate_sql_count_is_bounded_and_row_independent() -> None:
    """[RISK-01] 聚合 SQL 条数 ≤5，且**追加行数后不增长**（N+1 的直接反证）。"""
    baseline_count, baseline_sql = await _count_statements()
    assert baseline_count > 0, "未捕获到任何 SQL，测量本身失效"
    assert baseline_count <= MAX_AGGREGATE_SQL, f"单次聚合用了 {baseline_count} 条 SQL"

    extra = 12
    agent_id = uuid.UUID(
        str(_scalar("SELECT id FROM control.agent_definition WHERE tenant_id = :t LIMIT 1"))
    )
    actor_id = uuid.UUID(
        str(_scalar("SELECT id FROM control.platform_user WHERE tenant_id = :t LIMIT 1"))
    )
    _insert_extra_tasks(agent_id, actor_id, extra)

    grown_count, _ = await _count_statements()
    assert grown_count == baseline_count, (
        f"追加 {extra} 行后聚合 SQL 由 {baseline_count} 增至 {grown_count} —— 存在 N+1"
    )

    # 不读快照/缓存表：语句只落在四张 Owner 表上
    joined = " ".join(baseline_sql).lower()
    for table in ("control.agent_definition", "control.skill", "task.task_execution",
                  "task.task_schedule"):
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
        assert body["data"]["kpis"]["enabled_agents"] == seed.enabled_agents
        assert body["data"]["kpis"]["active_schedules"] == seed.active_schedules
        assert [item["name"] for item in body["data"]["next_schedules"]] == list(
            seed.next_schedule_names
        )
    finally:
        process.stop()
        assert process._process is not None and process._process.poll() is not None, (
            "no-redis 进程未退出（孤儿）"
        )


async def _count_metrics_statements() -> int:
    """与 `_count_statements` 同口径，但测量 `get_metrics()`（指标图聚合）。"""
    captured: list[str] = []
    engine = get_engine()

    def capture(*args: Any) -> None:
        captured.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with get_session_factory()() as session:
            await OverviewQueryService(session, tenant_id=TENANT).get_metrics(days=7)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    return len(captured)


async def test_metrics_trend_and_status_match_independent_reads(
    overview_stack: OverviewStack,
) -> None:
    """[S-05] 指标聚合：趋势为连续日窗（补零）、数值与独立 SQL 回读一致、状态分布总量一致。"""
    client = await console_login(overview_stack.console_url)
    try:
        response = await client.get("/api/v1/overview/metrics")
    finally:
        await client.aclose()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    data = body["data"]
    assert data["days"] == 7

    trend = data["task_trend"]
    assert len(trend) == 7
    days = [item["date"] for item in trend]
    assert days == sorted(days), "趋势必须按日期升序"
    assert (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days == 6, (
        "趋势窗口必须是连续 7 天（无数据日补零，不允许断日）"
    )

    # 与独立 SQL 回读一致：时区用**服务端返回值**，避免两端各自取时区造成口径漂移
    tz = data["timezone"]
    window_total = sum(item["total"] for item in trend)

    async def read_window(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            return int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t "
                        "AND NOT is_deleted "
                        "AND create_time >= ((now() AT TIME ZONE :tz)::date - 6) AT TIME ZONE :tz"
                    ),
                    {"t": TENANT, "tz": tz},
                )
            )

    assert window_total == run_db(read_window)

    status: dict[str, int] = data["task_status"]
    assert sum(status.values()) == _scalar(
        "SELECT count(*) FROM task.task_execution WHERE tenant_id = :t AND NOT is_deleted"
    )
    assert status.get("FAILED", 0) >= sum(item["failed"] for item in trend), (
        "趋势窗口内的失败数不得超过 FAILED 存量（窗口可能早于历史）"
    )


async def test_metrics_aggregate_sql_count_is_bounded_and_row_independent(
    overview_stack: OverviewStack,
) -> None:
    """[RISK-01 同口径] 指标聚合 SQL 条数有界，且追加行数后不增长（无 N+1）。"""
    baseline = await _count_metrics_statements()
    assert 0 < baseline <= MAX_AGGREGATE_SQL, f"指标聚合用了 {baseline} 条 SQL"

    agent_id = uuid.UUID(
        str(_scalar("SELECT id FROM control.agent_definition WHERE tenant_id = :t LIMIT 1"))
    )
    actor_id = uuid.UUID(
        str(_scalar("SELECT id FROM control.platform_user WHERE tenant_id = :t LIMIT 1"))
    )
    _insert_extra_tasks(agent_id, actor_id, 12)

    grown = await _count_metrics_statements()
    assert grown == baseline, f"追加行后指标聚合 SQL 由 {baseline} 增至 {grown} —— 存在 N+1"
