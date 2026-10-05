"""13-console-auth 真实验收环境：真实 Console 进程 + 真实 PostgreSQL + 租户级种子与清理。

- 服务是独立 uvicorn 子进程（真实进程、真实 HTTP），不覆盖任何业务路由；依赖缺失一律
  fail（`require`），不允许 skip 后冒充通过；
- 种子按 design §3.3 的两张表构造**可辨识的边界样本**：ADMIN/BUILDER 各一、`enabled=false`
  一个、`locked_until > now` 一个、以及一个 `expires_at` 剩余 <6h 的会话（供 B-02 的临界
  断言使用）——会话种子返回**明文令牌**，因为库内只存 sha256、测试必须能带着它发请求；
- 进程与 DB 原语复用 09-task-schedule 的真实验收栈（`ServiceProcess`/`free_port`/`run_db`）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
    ConsoleSession,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.task_schedule.environment import (
    READY_TIMEOUT_SEC,
    ServiceProcess,
    clear_engine_caches,  # noqa: F401  (对外转出，供用例收尾调用)
    free_port,
    require,
    run_db,
)

TENANT = f"console-auth-{uuid.uuid4()}"
ADMIN_USERNAME = f"auth-admin-{uuid.uuid4().hex[:8]}"
BUILDER_USERNAME = f"auth-builder-{uuid.uuid4().hex[:8]}"
DISABLED_USERNAME = f"auth-disabled-{uuid.uuid4().hex[:8]}"
LOCKED_USERNAME = f"auth-locked-{uuid.uuid4().hex[:8]}"
ADMIN_PASSWORD = "console-auth-admin-password"
BUILDER_PASSWORD = "console-auth-builder-password"
DISABLED_PASSWORD = "console-auth-disabled-password"
LOCKED_PASSWORD = "console-auth-locked-password"

# 平台默认 `auth.session_ttl_hours=12`（本栈不种 `control.platform_setting`，读取侧回落默认）。
# 会话窗口跨度恒为签发时 TTL（`issued_at = now - (SESSION_TTL - remaining)`）：滑动阈值是派生值
# `(expires_at - issued_at)/2`（ADR-12），若签发时间取 `now` 则跨度退化成剩余、永不过中点。
SESSION_TTL = timedelta(hours=12)
# 会话临界：剩余 3h（< 6h 阈值）→ `/auth/me` 应续期；另备一个恰 6h 的供边界断言
SESSION_REMAINING = timedelta(hours=3)
SESSION_EDGE_REMAINING = timedelta(hours=6)

CLEANUP_TABLES = ("control.console_session", "control.console_account")


@dataclass(frozen=True)
class AuthSeed:
    tenant_id: str
    admin_id: uuid.UUID
    admin_username: str
    admin_password: str
    builder_id: uuid.UUID
    builder_username: str
    builder_password: str
    disabled_username: str
    disabled_password: str
    locked_username: str
    locked_password: str
    locked_until: datetime
    builder_token: str
    edge_token: str


@dataclass
class AuthStack:
    console_url: str
    artifact_root: Path
    tenant_id: str
    seed: AuthSeed
    processes: dict[str, ServiceProcess] = field(default_factory=dict)


def artifact_root_dir(root: Path) -> Path:
    target = root / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _base_env(database_url: str, redis_url: str, root: Path) -> dict[str, str]:
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


def stop_auth_stack(processes: list[ServiceProcess]) -> None:
    """逆序停止真实子进程；任何失败路径都必须走到这里，绝不留下孤儿进程。"""
    for process in reversed(processes):
        process.stop()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> AuthSeed:
    now = datetime.now(UTC)
    builder_token = secrets.token_urlsafe(32)
    edge_token = secrets.token_urlsafe(32)
    async with factory() as session:
        async with session.begin():
            admin = ConsoleAccount(
                tenant_id=TENANT,
                username=ADMIN_USERNAME,
                display_name="Auth Admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
                enabled=True,
            )
            builder = ConsoleAccount(
                tenant_id=TENANT,
                username=BUILDER_USERNAME,
                display_name="Auth Builder",
                password_hash=hash_password(BUILDER_PASSWORD),
                role=ROLE_BUILDER,
                enabled=True,
            )
            disabled = ConsoleAccount(
                tenant_id=TENANT,
                username=DISABLED_USERNAME,
                display_name="Auth Disabled",
                password_hash=hash_password(DISABLED_PASSWORD),
                role=ROLE_BUILDER,
                enabled=False,
            )
            locked_until = now + timedelta(minutes=15)
            locked = ConsoleAccount(
                tenant_id=TENANT,
                username=LOCKED_USERNAME,
                display_name="Auth Locked",
                password_hash=hash_password(LOCKED_PASSWORD),
                role=ROLE_BUILDER,
                enabled=True,
                failed_attempts=0,
                locked_until=locked_until,
            )
            session.add_all([admin, builder, disabled, locked])
            await session.flush()
            session.add_all(
                [
                    ConsoleSession(
                        account_id=builder.id,
                        token_hash=_token_hash(builder_token),
                        issued_at=now - (SESSION_TTL - SESSION_REMAINING),
                        expires_at=now + SESSION_REMAINING,
                        last_seen_at=now,
                    ),
                    ConsoleSession(
                        account_id=builder.id,
                        token_hash=_token_hash(edge_token),
                        issued_at=now - (SESSION_TTL - SESSION_EDGE_REMAINING),
                        expires_at=now + SESSION_EDGE_REMAINING,
                        last_seen_at=now,
                    ),
                ]
            )
            return AuthSeed(
                tenant_id=TENANT,
                admin_id=admin.id,
                admin_username=ADMIN_USERNAME,
                admin_password=ADMIN_PASSWORD,
                builder_id=builder.id,
                builder_username=BUILDER_USERNAME,
                builder_password=BUILDER_PASSWORD,
                disabled_username=DISABLED_USERNAME,
                disabled_password=DISABLED_PASSWORD,
                locked_username=LOCKED_USERNAME,
                locked_password=LOCKED_PASSWORD,
                locked_until=locked_until,
                builder_token=builder_token,
                edge_token=edge_token,
            )


def seed_auth_tenant() -> AuthSeed:
    """写入可辨识的租户级种子（账号 + 两个临界会话），返回含明文令牌的描述符。"""
    return run_db(_seed)  # type: ignore[return-value]


def _statements() -> tuple[str, ...]:
    return (
        "DELETE FROM control.console_session WHERE account_id IN "
        "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
        "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
        "DELETE FROM control.console_account WHERE tenant_id = :t",
    )


def purge_tenant() -> None:
    """租户级清理：会话先于账号（FK 依赖），并清掉本模块产生的审计行；幂等。"""

    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            async with session.begin():
                for statement in _statements():
                    await session.execute(text(statement), {"t": TENANT})

    run_db(purge)


def count_tenant_rows(table: str) -> int:
    """按租户回读某表行数（真实 PostgreSQL）。"""

    async def count(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            value = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            return int(value or 0)

    return int(run_db(count))


def count_tenant_sessions() -> int:
    """`control.console_session` 没有 `tenant_id` 列（按 `account_id` 关联），
    故按「本租户账号名下的会话」计数，而不是按租户列直接过滤。"""

    async def count(factory: async_sessionmaker[AsyncSession]) -> int:
        async with factory() as session:
            value = await session.scalar(
                text(
                    "SELECT count(*) FROM control.console_session WHERE account_id IN "
                    "(SELECT id FROM control.console_account WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            return int(value or 0)

    return int(run_db(count))


def start_auth_stack(root: Path) -> tuple[AuthStack, list[ServiceProcess]]:
    """启动真实 Console 进程栈并写入种子；任何一步失败都不留孤儿进程。"""
    processes: list[ServiceProcess] = []
    try:
        stack = _boot(root, processes)
    except BaseException:
        stop_auth_stack(processes)
        raise
    return stack, processes


def _boot(root: Path, processes: list[ServiceProcess]) -> AuthStack:
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = artifact_root_dir(root)
    seed = seed_auth_tenant()
    console = ServiceProcess(
        name="console",
        module="muad_console_platform.main",
        port=free_port(),
        env=_base_env(database_url, redis_url, root),
        log_path=logs / "console.log",
    )
    processes.append(console)
    console.start()
    return AuthStack(
        console_url=console.url,
        artifact_root=artifact_root,
        tenant_id=TENANT,
        seed=seed,
        processes={process.name: process for process in processes},
    )
