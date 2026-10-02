"""[每次运行独立数据存储] 验收基座：本会话独占一个空库 + 一个独立 Redis DB。

**为什么需要**：验收各域共用一份 dev 库时，任务队列的 claim 与投递的选取都**不带租户谓词**
（Worker 是无状态通用工作池，任务行自带 `tenant_id`；`worker/claimer.py:20-27`、
`delivery/service.py:120-139`），而各栈的出站渠道端点又是**栈级 env**（`CHANNEL_PROBE_URL`
让 Gateway 换用 HTTP 探针适配器，`im-gateway/main.py:35-39`）。于是别的套件（或 dev 服务）
留下的可领取/待投递行会被本栈领走、并按**本栈**端点投递 —— 表现为「我的探针里出现别人的投递」，
断言随机红且**单跑绿、串跑红**（2026-10-02 实测：`verify-e2e` 的 14 条 verifier 顺序执行下
task_schedule 的投递用例必红，单独跑全绿）。给每次运行一份空库 + 独立 Redis DB 后，这类
跨套件干扰在结构上不可能发生。

**做法与 CI 同口径**（`.github/workflows/check.yml:44-48`）：现场生成只含目标 DSN 的 alembic ini，
用 `migrations/db_migrate.py -c <ini>` 迁到 head —— 迁移只认 ini、不读环境变量。

**前提**：连接角色需要 CREATEDB。本地一次性执行 `ALTER ROLE muad CREATEDB;`（用超级用户
`mmuser`）。缺权限时本 fixture 直接失败并打印该提示，**不静默退回共享库** —— 退回等于隔离失效。

**逃生阀**：`MUAD_ACCEPTANCE_SHARED_DB=1` 时不建库，沿用环境里现有的库（仅用于排查对比）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from muad_common import SharedSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
# 独立 Redis 标号区间（与 dev 的 db 0 分开；同机并发运行按 pid 落在不同号上）
REDIS_DB_START = 10
REDIS_DB_COUNT = 6
_MANAGED_ENV = ("DATABASE_URL", "REDIS_URL")


def _run_in_thread(factory: Callable[[], Any]) -> Any:
    """在**独立线程**的事件循环里跑协程。

    不能用 `asyncio.run`：它会把主线程的事件循环置空，本会话里其后所有异步套件会集体报
    `no current event loop`（实测 95 failed）。同口径先例见各域 environment 的 `run_async`/`run_db`。
    """
    box: list[Any] = []
    failure: list[BaseException] = []

    def target() -> None:
        loop = asyncio.new_event_loop()
        try:
            box.append(loop.run_until_complete(factory()))
        except BaseException as exc:  # noqa: BLE001 - 跨线程回抛原始异常
            failure.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=target, name="acceptance-datastore")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return box[0] if box else None


def _swap_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _redis_url_with_db(url: str, index: int) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{index}"))


def _asyncpg_dsn(url: str) -> str:
    """`postgresql+asyncpg://` → `postgresql://`（asyncpg 直连不接受 SQLAlchemy 方言前缀）。"""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _create_database(dsn: str, name: str) -> None:
    async def create() -> None:
        connection = await asyncpg.connect(dsn, timeout=10)
        try:
            await connection.execute(f'CREATE DATABASE "{name}"')
        finally:
            await connection.close()

    _run_in_thread(create)


def _drop_database(dsn: str, name: str) -> None:
    async def drop() -> None:
        connection = await asyncpg.connect(dsn, timeout=10)
        try:
            # FORCE：断掉本会话遗留连接，否则 DROP 会因 "database is being accessed" 失败
            await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await connection.close()

    _run_in_thread(drop)


def _migrate_to_head(database_url: str, workdir: Path) -> None:
    ini = workdir / "alembic.acceptance.ini"
    ini.write_text(
        "[alembic]\n"
        "script_location = migrations\n"
        "prepend_sys_path = .\n"
        f"sqlalchemy.url = {database_url}\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "migrations/db_migrate.py", "-c", str(ini)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "acceptance 临时库迁移失败：\n"
            f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
        )


@pytest.fixture(scope="session", autouse=True)
def isolated_datastores() -> Iterator[None]:
    """本会话独占一个空库 + 独立 Redis DB；结束时丢弃。"""
    if os.environ.get("MUAD_ACCEPTANCE_SHARED_DB") == "1":
        yield
        return

    settings = SharedSettings()
    base_database_url = settings.require_database_url()
    base_redis_url = settings.require_redis_url()
    name = f"muad_acc_{uuid.uuid4().hex[:10]}"
    admin_dsn = _asyncpg_dsn(base_database_url)

    try:
        _create_database(admin_dsn, name)
    except asyncpg.InsufficientPrivilegeError as exc:  # pragma: no cover - 环境前提
        pytest.fail(
            "验收需要独立数据库，但当前角色没有 CREATEDB：\n"
            f"  {exc}\n"
            "一次性授权：以超级用户执行 `ALTER ROLE muad CREATEDB;`（本地 dev 库用 mmuser）；\n"
            "或先用 `MUAD_ACCEPTANCE_SHARED_DB=1` 退回共享库排查（不推荐长期使用）。"
        )

    saved = {key: os.environ.get(key) for key in _MANAGED_ENV}
    tmpdir = Path(tempfile.mkdtemp(prefix="muad-acceptance-"))
    # 必须同时改 **os.environ**：各域套件的 seed 是进程内直连 DB、走 SharedSettings()，
    # 只注入子进程会出现「seed 写进旧库、服务读新库」的裂脑，每个域都会红。
    os.environ["DATABASE_URL"] = _swap_database(base_database_url, name)
    os.environ["REDIS_URL"] = _redis_url_with_db(
        base_redis_url, REDIS_DB_START + (os.getpid() % REDIS_DB_COUNT)
    )
    try:
        _migrate_to_head(os.environ["DATABASE_URL"], tmpdir)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmpdir, ignore_errors=True)
        _drop_database(admin_dsn, name)
