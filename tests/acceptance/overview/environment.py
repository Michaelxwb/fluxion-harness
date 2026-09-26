"""12-overview-dashboard 真实验收环境：真实 Console 进程 + 真实 PostgreSQL + 租户级种子与清理。

- 服务是独立 uvicorn 子进程（真实进程、真实 HTTP），不覆盖任何业务路由；依赖缺失一律
  fail（`require`），不允许 skip 后冒充通过；
- **按需最小栈**：概览是只读聚合（设计 §3.2.1），S-01 的真实边界是
  「真实 Console HTTP → 四张 Owner 表(PostgreSQL)」，故不启动 Runtime/Worker/Gateway
  与 LLM 探针 —— 与本模块无关的进程只会引入无关依赖（口径同 11-audit-observability
  对 Gateway 的处理）；
- 任务/定时数据由本环境直接写入真实表，使 S-01 的断言有真实数据可依；
- 进程与 DB 原语复用 09-task-schedule 的真实验收栈（`ServiceProcess`/`free_port`/`run_db`）。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
    Skill,
)
from muad_contracts import DeliveryRouteInput
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.task_schedule.environment import (
    CONTROL_CLEANUP,
    READY_TIMEOUT_SEC,
    TASK_CLEANUP,
    ServiceProcess,
    clear_engine_caches,  # noqa: F401  (对外转出，供用例收尾调用)
    free_port,
    require,
    run_db,
)

TENANT = f"overview-acceptance-{uuid.uuid4()}"
ADMIN_USERNAME = f"overview-admin-{uuid.uuid4().hex[:8]}"
ADMIN_PASSWORD = "overview-acceptance-password"

# Console 账号与会话：CONTROL_CLEANUP 不含（09/11 栈的租户没有 Console 账号），
# 本环境种了可登录 admin 供 S-01 使用，故自行清理；会话先于账号（FK 依赖）。
CONSOLE_CLEANUP = (
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
)

# 种子规模：全部取可辨识的数字，便于用例逐表回读并算出期望 KPI。
SEED_AGENTS_ENABLED = 3
SEED_AGENTS_DISABLED = 2
SEED_AGENTS_DELETED = 1
SEED_SKILLS_ENABLED = 4
SEED_SKILLS_DISABLED = 2
SEED_TASKS_ACTIVE = 2  # RUNNING / WAITING
SEED_TASKS_TERMINAL = 3  # SUCCEEDED / FAILED / CANCELLED
SEED_SCHEDULES_ACTIVE_WITH_NEXT = 2
SEED_SCHEDULES_ACTIVE_NO_NEXT = 1
SEED_SCHEDULES_PAUSED = 1

AGENT_NAME = "策略检查助手"
ACTOR_NAME = "张三"

# purge 必须清空的租户级表（`count_tenant_rows` 的白名单来源）。
# `control.console_session` 不在此列：它按 `account_id` 关联、没有 `tenant_id` 列，
# 由 CONTROL_CLEANUP 经子查询删除，无法按租户直接计数。
CLEANUP_TABLES = (
    "control.console_account",
    "control.agent_definition",
    "control.skill",
    "control.platform_user",
    "control.model_definition",
    "task.task_execution",
    "task.task_schedule",
    "task.delivery_route",
)

__all__ = [
    "ACTOR_NAME",
    "ADMIN_PASSWORD",
    "ADMIN_USERNAME",
    "AGENT_NAME",
    "CLEANUP_TABLES",
    "CONSOLE_CLEANUP",
    "OverviewSeed",
    "OverviewStack",
    "READY_TIMEOUT_SEC",
    "SEED_AGENTS_ENABLED",
    "TENANT",
    "clear_engine_caches",
    "console_login",
    "count_tenant_rows",
    "purge_tenant",
    "start_overview_stack",
    "stop_overview_stack",
    "wait_ready",
]


@dataclass(frozen=True)
class OverviewSeed:
    """种子写完后可直接与 API 响应比对的期望值。"""

    enabled_agents: int
    enabled_skills: int
    active_tasks: int
    active_schedules: int
    task_statuses_desc: tuple[str, ...]  # recent_tasks 的期望状态序列（create_time DESC）
    next_schedule_names: tuple[str, ...]  # next_schedules 的期望名字（next_fire_at ASC）


@dataclass
class OverviewStack:
    console_url: str
    artifact_root: Path
    tenant_id: str
    seed: OverviewSeed
    processes: dict[str, ServiceProcess] = field(default_factory=dict)


def artifact_root_dir(root: Path) -> Path:
    target = root / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _base_env(database_url: str, redis_url: str, root: Path) -> dict[str, str]:
    """服务进程环境：artifact / skill-cache 根钉在验收临时目录，其余继承真实进程环境。"""
    skill_cache_root = root / "skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root_dir(root)),
        "SKILL_CACHE_ROOT": str(skill_cache_root),
        "DEFAULT_TENANT_ID": TENANT,
    }


async def wait_ready(url: str, timeout: float = READY_TIMEOUT_SEC) -> httpx.Response:
    """等待真实 HTTP 探针就绪：200 返回响应体，超时抛错（不允许静默放行）。"""
    deadline = time.monotonic() + timeout
    last_status: int | None = None
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
            except httpx.HTTPError:
                last_status = None
            else:
                last_status = response.status_code
                if last_status == 200:
                    return response
            await asyncio.sleep(0.2)
    raise RuntimeError(f"{url} 未在 {timeout}s 内就绪（最后状态码: {last_status}）")


def stop_overview_stack(processes: list[ServiceProcess]) -> None:
    """逆序停止真实子进程；任何失败路径都必须走到这里，绝不留下孤儿进程。"""
    for process in reversed(processes):
        process.stop()


async def console_login(
    base_url: str, *, username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD
) -> httpx.AsyncClient:
    """真实登录并持有会话 cookie + CSRF 头 + 租户头的客户端（调用方负责 aclose）。"""
    client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    client.headers["X-Tenant-Id"] = TENANT
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        await client.aclose()
        raise RuntimeError(f"Console 登录失败: {response.status_code} {response.text[:300]}")
    csrf = client.cookies.get("muad_csrf")
    if not csrf:
        await client.aclose()
        raise RuntimeError("登录后未拿到 CSRF cookie")
    client.headers["X-CSRF-Token"] = csrf
    return client


async def _insert_control_rows(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> tuple[list[uuid.UUID], uuid.UUID, uuid.UUID]:
    async with factory() as session:
        async with session.begin():
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"model-{uuid.uuid4().hex[:8]}",
                name="Overview Model",
                model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1",
                enabled=True,
            )
            session.add(model)
            await session.flush()
            agent_ids: list[uuid.UUID] = []
            for index in range(SEED_AGENTS_ENABLED + SEED_AGENTS_DISABLED + SEED_AGENTS_DELETED):
                agent = AgentDefinition(
                    tenant_id=TENANT,
                    key=f"agent-{uuid.uuid4().hex[:8]}",
                    name=AGENT_NAME if index == 0 else f"Agent {index}",
                    instructions="overview acceptance",
                    model_id=model.id,
                    revision=1,
                    enabled=index < SEED_AGENTS_ENABLED,
                    is_deleted=index >= SEED_AGENTS_ENABLED + SEED_AGENTS_DISABLED,
                )
                session.add(agent)
                await session.flush()
                agent_ids.append(agent.id)
            for index in range(SEED_SKILLS_ENABLED + SEED_SKILLS_DISABLED):
                session.add(
                    Skill(
                        tenant_id=TENANT,
                        key=f"skill-{uuid.uuid4().hex[:8]}",
                        name=f"Skill {index}",
                        description="overview acceptance",
                        enabled=index < SEED_SKILLS_ENABLED,
                    )
                )
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name=ACTOR_NAME,
            )
            session.add(user)
            await session.flush()
            account = ConsoleAccount(
                tenant_id=TENANT,
                username=ADMIN_USERNAME,
                display_name="Overview Admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
            )
            session.add(account)
            await session.flush()
            return agent_ids, user.id, model.id


async def _insert_task_rows(
    factory: async_sessionmaker[AsyncSession], agent_id: uuid.UUID, actor_id: uuid.UUID, now: datetime
) -> None:
    active = ("RUNNING", "WAITING")
    terminal = ("SUCCEEDED", "FAILED", "CANCELLED")
    async with factory() as session:
        async with session.begin():
            for offset, status in enumerate((*terminal, *active)):
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
                        trigger_type="SCHEDULED",
                        execution_mode="ASYNC",
                        task_type="SKILL",
                        status=status,
                        input_json={},
                        execution_snapshot_schema_version=1,
                        execution_snapshot_json={"schema_version": 1},
                        snapshot_hash="sha256:" + "d" * 64,
                        idempotency_key=f"overview-acceptance-{task_id}",
                        priority=100,
                        attempt=0,
                        max_attempts=3,
                        not_before=now,
                        deadline_at=now + timedelta(hours=24),
                        started_at=now,
                        delivery_mode="NONE",
                        delivery_status="NONE",
                        delivery_key=f"task:{task_id}:final",
                        delivery_attempts=0,
                        # 递增 create_time：recent_tasks 的 DESC 排序可确定断言
                        create_time=now - timedelta(minutes=10 - offset),
                    )
                )
            route_id = await upsert_delivery_route(
                session,
                tenant_id=TENANT,
                platform_user_id=actor_id,
                route=DeliveryRouteInput(
                    channel="WECOM", bot_id=f"overview-bot-{uuid.uuid4().hex[:8]}",
                    external_user_id="overview-acceptance",
                ),
            )
            names = [f"启用定时 {index + 1}" for index in range(SEED_SCHEDULES_ACTIVE_WITH_NEXT)]
            names += [f"无下次触发 {index + 1}" for index in range(SEED_SCHEDULES_ACTIVE_NO_NEXT)]
            names += [f"暂停定时 {index + 1}" for index in range(SEED_SCHEDULES_PAUSED)]
            for index, name in enumerate(names):
                is_next_batch = index < SEED_SCHEDULES_ACTIVE_WITH_NEXT
                session.add(
                    TaskSchedule(
                        tenant_id=TENANT,
                        name=name,
                        agent_id=agent_id,
                        actor_user_id=actor_id,
                        intent_key="policy_check",
                        skill_id=uuid.uuid4(),
                        input_template_json={},
                        schedule_type="CRON",
                        cron_expr="0 9 * * *",
                        timezone="Asia/Shanghai",
                        delivery_route_id=route_id,
                        status="PAUSED" if index >= len(names) - SEED_SCHEDULES_PAUSED else "ACTIVE",
                        next_fire_at=(
                            now + timedelta(hours=index + 1) if is_next_batch else None
                        ),
                        revision=1,
                    )
                )


def seed_overview_tenant() -> OverviewSeed:
    """写入可辨识的租户级种子，返回与 API 响应直接比对的期望值。"""
    now = datetime.now(UTC)

    async def seed(factory: async_sessionmaker[AsyncSession]) -> OverviewSeed:
        agent_ids, actor_id, _model_id = await _insert_control_rows(factory, now)
        await _insert_task_rows(factory, agent_ids[0], actor_id, now)
        task_statuses = ("RUNNING", "WAITING", "CANCELLED", "FAILED", "SUCCEEDED")
        return OverviewSeed(
            enabled_agents=SEED_AGENTS_ENABLED,
            enabled_skills=SEED_SKILLS_ENABLED,
            active_tasks=SEED_TASKS_ACTIVE,
            active_schedules=SEED_SCHEDULES_ACTIVE_WITH_NEXT + SEED_SCHEDULES_ACTIVE_NO_NEXT,
            task_statuses_desc=task_statuses,
            next_schedule_names=tuple(
                f"启用定时 {index + 1}" for index in range(SEED_SCHEDULES_ACTIVE_WITH_NEXT)
            ),
        )

    return run_db(seed)


def _run_statements(statements: tuple[str, ...]) -> None:
    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            async with session.begin():
                for statement in statements:
                    await session.execute(text(statement), {"t": TENANT})

    run_db(purge)


def purge_tenant() -> None:
    """租户级清理：先子行再父行，幂等可重复执行。

    `CONSOLE_CLEANUP` 是本模块自带的：09/11 的租户不带 Console 账号，故
    `CONTROL_CLEANUP` 不含 `console_account`/`console_session`；本环境种了可登录的
    admin 账号（S-01 需要真实会话），必须自行清理。会话先于账号（FK 依赖）。
    """
    _run_statements((*CONSOLE_CLEANUP, *TASK_CLEANUP, *CONTROL_CLEANUP))


def count_tenant_rows(table: str) -> int:
    """按租户回读某表行数（真实 PostgreSQL，用于清理与种子断言）。"""

    async def count(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            value = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            return int(value or 0)

    return int(run_db(count))


def start_overview_stack(root: Path) -> tuple[OverviewStack, list[ServiceProcess]]:
    """启动真实 Console 进程栈并写入种子；任何一步失败都不留孤儿进程。"""
    processes: list[ServiceProcess] = []
    try:
        stack = _boot(root, processes)
    except BaseException:
        stop_overview_stack(processes)
        raise
    return stack, processes


def _boot(root: Path, processes: list[ServiceProcess]) -> OverviewStack:
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = artifact_root_dir(root)
    seed = seed_overview_tenant()
    console = ServiceProcess(
        name="console",
        module="muad_console_platform.main",
        port=free_port(),
        env=_base_env(database_url, redis_url, root),
        log_path=logs / "console.log",
    )
    processes.append(console)
    console.start()
    return OverviewStack(
        console_url=console.url,
        artifact_root=artifact_root,
        tenant_id=TENANT,
        seed=seed,
        processes={process.name: process for process in processes},
    )
