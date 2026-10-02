"""[每轮独立数据存储] 建库 / 迁移 / 清理的可复用实现。

**唯一实现处**：pytest 侧（`tests/acceptance/conftest.py` 的 session fixture）与 Playwright 侧
（`e2e/support/isolated-datastores.ts` 调本模块 CLI）共用这里的逻辑，避免两份建库代码漂移。

**做法与 CI 同口径**（`.github/workflows/check.yml:44-48`）：现场生成只含目标 DSN 的 alembic ini，
用 `migrations/db_migrate.py -c <ini>` 迁到 head —— 迁移只认 ini、不读环境变量。

**CLI**：
  uv run python -m tests.acceptance.datastores start [--seed-console-admin ...]
      → 建空库 + 迁到 head（可选预置 Console 管理员），stdout 输出 JSON
        {"database_url": …, "redis_url": …, "name": …}
  uv run python -m tests.acceptance.datastores stop <name>
      → DROP DATABASE … WITH (FORCE)

**前提**：连接角色需要 CREATEDB。缺权限时 `create_datastore` 抛 `DatastorePrivilegeError`，
调用方**不得静默退回共享库** —— 退回等于隔离失效。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from muad_common import SharedSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
# 独立 Redis 标号区间（与 dev 的 db 0 分开；同机并发运行按 pid 落在不同号上）
REDIS_DB_START = 10
REDIS_DB_COUNT = 6
DEFAULT_PREFIX = "muad_acc_"


class DatastorePrivilegeError(RuntimeError):
    """当前角色没有 CREATEDB，无法建独立库（提示见消息）。"""


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


def new_database_name(prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def isolated_redis_url(base_redis_url: str) -> str:
    """按 pid 落到 10–15 号 Redis DB（同机并发运行互不撞号）。"""
    return _redis_url_with_db(base_redis_url, REDIS_DB_START + (os.getpid() % REDIS_DB_COUNT))


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


def create_datastore(prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """建一个空库 + 独立 Redis DB，库迁到 head。

    返回 `database_url` / `redis_url` / `name`，以及 `admin_database_url`（**建库前的基准 DSN**）。
    后者是必需的：调用方随后会把 `DATABASE_URL` 覆盖成临时库，drop 时必须用基准 DSN 连回
    管理库，否则会连到临时库自身、报 `cannot drop the currently open database`。
    """
    settings = SharedSettings()
    base_database_url = settings.require_database_url()
    base_redis_url = settings.require_redis_url()
    name = new_database_name(prefix)
    admin_dsn = _asyncpg_dsn(base_database_url)

    try:
        _create_database(admin_dsn, name)
    except asyncpg.InsufficientPrivilegeError as exc:
        raise DatastorePrivilegeError(
            "验收需要独立数据库，但当前角色没有 CREATEDB：\n"
            f"  {exc}\n"
            "一次性授权：以超级用户执行 `ALTER ROLE muad CREATEDB;`（本地 dev 库用 mmuser）；\n"
            "或先用 `MUAD_ACCEPTANCE_SHARED_DB=1` 退回共享库排查（不推荐长期使用）。"
        ) from exc

    database_url = _swap_database(base_database_url, name)
    tmpdir = Path(tempfile.mkdtemp(prefix="muad-acceptance-"))
    try:
        _migrate_to_head(database_url, tmpdir)
    except BaseException:
        # 迁移失败也要丢弃半成品库，避免残留
        _drop_database(admin_dsn, name)
        raise
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "database_url": database_url,
        "redis_url": isolated_redis_url(base_redis_url),
        "name": name,
        "admin_database_url": base_database_url,
    }


def drop_datastore(name: str, admin_database_url: str | None = None) -> None:
    """丢弃临时库（FORCE 断连）。`admin_database_url` 缺省时取环境里的基准 `DATABASE_URL`。"""
    base = admin_database_url or SharedSettings().require_database_url()
    _drop_database(_asyncpg_dsn(base), name)


def seed_console_admin(
    database_url: str,
    *,
    username: str,
    password: str,
    tenant: str = "default",
) -> None:
    """用 Console 的 create-admin CLI 在空库里预置可登录管理员。

    CLI 走 `get_session_factory()`（读 DATABASE_URL），故只覆盖 DATABASE_URL 传给子进程即可；
    不设 DEFAULT_TENANT_ID 无关紧要：`--tenant` 显式指定租户。
    """
    env = {**os.environ, "DATABASE_URL": database_url}
    completed = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "from muad_console_platform.cli import main; main(__import__('sys').argv[1:])",
            "create-admin",
            "--username",
            username,
            "--password",
            password,
            "--tenant",
            tenant,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "预置 Console 管理员失败：\n"
            f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
        )


def _cmd_start(args: argparse.Namespace) -> int:
    info = create_datastore()
    if args.seed_console_admin:
        seed_console_admin(
            info["database_url"],
            username=args.admin_username,
            password=args.admin_password,
            tenant=args.admin_tenant,
        )
    print(json.dumps(info))
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    drop_datastore(args.name, args.admin_url)
    print(json.dumps({"dropped": args.name}))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muad_acceptance_datastores")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="create an empty migrated database + isolated Redis DB")
    start.add_argument("--seed-console-admin", action="store_true")
    start.add_argument("--admin-username", default="admin")
    start.add_argument("--admin-password", required=False)
    start.add_argument("--admin-tenant", default="default")
    start.set_defaults(handler=_cmd_start)

    stop = subparsers.add_parser("stop", help="drop a temporary database")
    stop.add_argument("name")
    stop.add_argument(
        "--admin-url",
        default=None,
        help="建库前的基准 DATABASE_URL（调用方已把 DATABASE_URL 覆盖成临时库时必须传）",
    )
    stop.set_defaults(handler=_cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
