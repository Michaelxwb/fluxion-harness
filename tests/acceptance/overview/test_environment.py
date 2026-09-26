"""[B-202] 概览验收环境与种子清理：真实 Console 进程 + 真实 PostgreSQL + 租户级种子。

真实边界：独立 uvicorn 子进程（真实进程与真实 HTTP）+ 真实 PostgreSQL 逐表回读；
不 mock 业务服务，不覆盖业务路由。用例顺序即依赖顺序（先就绪 → 再种子 → 最后清理）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.acceptance.overview.environment import (
    ADMIN_USERNAME,
    AGENT_NAME,
    CLEANUP_TABLES,
    SEED_AGENTS_DISABLED,
    SEED_AGENTS_ENABLED,
    SEED_SKILLS_ENABLED,
    SEED_TASKS_ACTIVE,
    SEED_TASKS_TERMINAL,
    TENANT,
    OverviewStack,
    clear_engine_caches,
    console_login,
    count_tenant_rows,
    purge_tenant,
    start_overview_stack,
    stop_overview_stack,
    wait_ready,
)

EXPECTED_ROWS = {
    "control.model_definition": 1,
    "control.console_account": 1,
    "control.platform_user": 1,
    "control.agent_definition": SEED_AGENTS_ENABLED + SEED_AGENTS_DISABLED + 1,
    "control.skill": SEED_SKILLS_ENABLED + 2,
    "task.task_execution": SEED_TASKS_ACTIVE + SEED_TASKS_TERMINAL,
    "task.task_schedule": 4,
    "task.delivery_route": 1,
}


@pytest.fixture(scope="module")
def overview_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OverviewStack]:
    root = tmp_path_factory.mktemp("overview-acceptance")
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


async def test_b202_stack_boots_and_dependencies_are_ready(overview_stack: OverviewStack) -> None:
    """真实进程 + 真实依赖：`/healthz` 存活、`/readyz` 依赖就绪（PG 不可用时为 503）。

    探针经 api-kit 封套返回，故判读 `data.status`（§1.3 统一 Envelope）。
    """
    healthz = await wait_ready(f"{overview_stack.console_url}/healthz")
    assert healthz.json()["data"]["status"] == "ok"
    readyz = await wait_ready(f"{overview_stack.console_url}/readyz")
    assert readyz.json()["data"]["status"] == "ready", readyz.text


def test_b202_seed_produces_expected_rows(overview_stack: OverviewStack) -> None:
    """种子逐表回读：行数恰为期望值 —— 共享开发库里还有其它租户的数据，能取到**精确**
    数目即证明按 `tenant_id` 隔离生效（不串租户）。"""
    for table, expected in EXPECTED_ROWS.items():
        assert count_tenant_rows(table) == expected, f"{table} 行数与种子不符"


def test_b202_seed_expectations_match_design_semantics(overview_stack: OverviewStack) -> None:
    """种子给出的期望 KPI 与列表口径必须与 design §3.2.1 一致（S-01 将据此比对响应）。"""
    seed = overview_stack.seed
    assert (seed.enabled_agents, seed.enabled_skills) == (SEED_AGENTS_ENABLED, SEED_SKILLS_ENABLED)
    assert seed.active_tasks == SEED_TASKS_ACTIVE
    # 三个 ACTIVE Schedule 都计入 KPI（含无 next_fire_at 的那个）
    assert seed.active_schedules == 3
    # 但列表只含带 next_fire_at 的两条，且按 next_fire_at ASC
    assert seed.next_schedule_names == ("启用定时 1", "启用定时 2")
    # recent_tasks 不过滤状态：5 条全在（create_time DESC）
    assert len(seed.task_statuses_desc) == SEED_TASKS_ACTIVE + SEED_TASKS_TERMINAL


async def test_b202_seeded_admin_can_authenticate(overview_stack: OverviewStack) -> None:
    """真实登录（会话 + CSRF）可用 —— S-01 依赖同一入口，故在此先钉住。"""
    client = await console_login(overview_stack.console_url)
    try:
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["data"]["username"] == ADMIN_USERNAME
    finally:
        await client.aclose()


def test_b202_purge_is_idempotent_and_leaves_no_residue(overview_stack: OverviewStack) -> None:
    """清理幂等且清空本模块全部租户级表（含 task 子行与 FK 依赖顺序）。"""
    tenant_agents = count_tenant_rows("control.agent_definition")
    assert tenant_agents > 0, "清理前应仍有种子数据（否则断言的灵敏度不足）"

    purge_tenant()
    purge_tenant()

    for table in CLEANUP_TABLES:
        assert count_tenant_rows(table) == 0, f"{table} 清理后仍有残留"
    # 种子里的 Agent 名字不应再可查到（非空转：以具体值为锚）
    assert AGENT_NAME not in _agent_names_of_tenant()


def _agent_names_of_tenant() -> set[str]:
    from sqlalchemy import text

    from tests.acceptance.task_schedule.environment import run_db

    async def read(factory: object) -> set[str]:
        async with factory() as session:  # type: ignore[operator]
            rows = await session.scalars(
                text("SELECT name FROM control.agent_definition WHERE tenant_id = :t"),
                {"t": TENANT},
            )
            return set(rows.all())

    return set(run_db(read))
