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
      → DROP DATABASE … WITH (FORCE)，并归还本次占用的 Redis 号位

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
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import redis.asyncio
from muad_common import SharedSettings
from redis.exceptions import RedisError

REPO_ROOT = Path(__file__).resolve().parents[2]
# 独立 Redis 标号区间（与 dev 的 db 0 分开）。号位靠**原子占位**分配，不靠 pid 取模 ——
# 用 pid 取模时两个并发运行只要同余就共用同一个 DB（1/6 概率），隔离会退化成「单跑绿、串跑红」。
REDIS_DB_START = 10
REDIS_DB_COUNT = 6
# 占号键（写在**被占的那个号**里）：值为本次的库名，收尾按值释放，互踩不到别人的号。
REDIS_CLAIM_KEY = "muad:acceptance:db-claim"
# 异常中断（未走到收尾）的运行靠 TTL 自动让出号位；取一个明显长于单轮验收的值。
REDIS_CLAIM_TTL_SEC = 7200
DEFAULT_PREFIX = "muad_acc_"


class DatastoreUnavailableError(RuntimeError):
    """当前环境无法提供独立数据存储（权限不足，或 Redis 号位耗尽）。"""


class DatastorePrivilegeError(DatastoreUnavailableError):
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


def _claim_redis_db(url_with_db: str, owner: str) -> bool:
    """在该号上原子占位（`SET NX EX`）。True = 本运行独占该号。"""

    async def claim() -> bool:
        client = redis.asyncio.from_url(url_with_db)
        try:
            return bool(await client.set(REDIS_CLAIM_KEY, owner, nx=True, ex=REDIS_CLAIM_TTL_SEC))
        finally:
            await client.aclose()

    return bool(_run_in_thread(claim))


def _release_redis_db(redis_url: str, owner: str) -> None:
    """释放**值等于本次库名**的占号；值属于别的运行的，一律不动。"""

    async def release() -> None:
        encoded = owner.encode()
        for index in range(REDIS_DB_START, REDIS_DB_START + REDIS_DB_COUNT):
            client = redis.asyncio.from_url(_redis_url_with_db(redis_url, index))
            try:
                if (await client.get(REDIS_CLAIM_KEY)) == encoded:
                    await client.delete(REDIS_CLAIM_KEY)
            finally:
                await client.aclose()

    _run_in_thread(release)


def isolated_redis_url(base_redis_url: str, owner: str) -> str:
    """占一个**未被占用**的 10–15 号 Redis DB 并返回指向它的 URL。

    号位靠原子占位分配，不靠 `pid % 6` 取模：取模时两个并发运行只要 pid 同余就**共用同一个
    DB**（1/6 概率），而 Redis 承载去重键与队列 —— 共用即隔离失效，退化成「单跑绿、串跑红」，
    正是本机制要消除的那类症状。`owner` 传本次的库名，收尾时按值释放。
    """
    first = os.getpid() % REDIS_DB_COUNT
    for offset in range(REDIS_DB_COUNT):
        index = REDIS_DB_START + (first + offset) % REDIS_DB_COUNT
        candidate = _redis_url_with_db(base_redis_url, index)
        if _claim_redis_db(candidate, owner):
            return candidate
    raise DatastoreUnavailableError(
        f"{REDIS_DB_START}–{REDIS_DB_START + REDIS_DB_COUNT - 1} 号 Redis DB 全被占用："
        f"同机并发验收超过 {REDIS_DB_COUNT} 个。等其中一个跑完（收尾会释放占号，"
        f"异常中断的由 {REDIS_CLAIM_TTL_SEC}s TTL 兜底）后重试；确认无残留时也可手动清：\n"
        f"  for n in $(seq {REDIS_DB_START} {REDIS_DB_START + REDIS_DB_COUNT - 1}); "
        f"do redis-cli -n $n DEL {REDIS_CLAIM_KEY}; done"
    )


def _create_database(dsn: str, name: str) -> None:
    async def create() -> None:
        connection = await asyncpg.connect(dsn, timeout=10)
        try:
            await connection.execute(f'CREATE DATABASE "{name}"')
        finally:
            await connection.close()

    _run_in_thread(create)


def _drop_database(dsn: str, name: str, *, attempts: int = 4) -> None:
    """`DROP DATABASE ... WITH (FORCE)`，对后台进程竞态做有界重试。

    PG 15 下 FORCE 会终止目标库上的全部后端；非超级用户（无 `pg_signal_backend`）在目标库上
    恰好有 autovacuum 等后台进程时会被拒（`must be a member of the role whose process is being
    terminated or member of pg_signal_backend`）。这类进程是瞬时的：短暂等待后重试即可收敛；
    重试仍失败则照常抛出，不静默吞掉（2026-10-09 实测一次 teardown 偶发）。
    """

    async def drop() -> None:
        connection = await asyncpg.connect(dsn, timeout=10)
        try:
            # FORCE：断掉本会话遗留连接，否则 DROP 会因 "database is being accessed" 失败
            await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await connection.close()

    for attempt in range(attempts):
        try:
            _run_in_thread(drop)
            return
        except asyncpg.InsufficientPrivilegeError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


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

    # 先占 Redis 号位：抢不到就立刻失败，此时还没有库需要回收。之后每条失败路径都要把号位
    # 还回去 —— 否则异常一次就永久吃掉一个号（只靠 TTL 兜底会让并发额度悄悄缩水）。
    redis_url = isolated_redis_url(base_redis_url, name)
    try:
        _create_database(admin_dsn, name)
    except asyncpg.InsufficientPrivilegeError as exc:
        _release_redis_db(base_redis_url, name)
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
        # 迁移失败也要丢弃半成品库 + 归还 Redis 号位，避免残留
        _drop_database(admin_dsn, name)
        _release_redis_db(base_redis_url, name)
        raise
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "database_url": database_url,
        "redis_url": redis_url,
        "name": name,
        "admin_database_url": base_database_url,
    }


def drop_datastore(
    name: str, admin_database_url: str | None = None, redis_url: str | None = None
) -> None:
    """丢弃临时库（FORCE 断连）并**归还 Redis 号位**。

    两个 URL 缺省时都取环境里的基准值：`_redis_url_with_db` 只替换路径，故传进来的 URL
    带的是哪个 Redis 号都无所谓，只要指向同一实例（pytest 侧收尾前已恢复原 env、Playwright
    侧 CLI 子进程继承的是被覆盖过的 env，两条路径都成立）。
    """
    base = admin_database_url or SharedSettings().require_database_url()
    _drop_database(_asyncpg_dsn(base), name)
    try:
        _release_redis_db(redis_url or SharedSettings().require_redis_url(), name)
    except RedisError as exc:
        # 不阻塞收尾（占号有 TTL 兜底），但显式告警而非静默吞掉
        print(f"[acceptance] Redis 占号释放失败（{name}）：{exc}", file=sys.stderr)


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


def seed_platform_settings(
    database_url: str, tenant_id: str, settings: Mapping[str, Any], *, revision: int = 1
) -> None:
    """按租户种一行平台设置（design §3.3），与生产同构。

    acceptance 栈原先靠子进程 env 注入的业务默认（`delivery_backoff_base_sec` 等）已迁到
    `control.platform_setting`，env 注入静默失效 —— 栈启动时改为按**本栈租户**写入一行
    revision=1 的设置。`settings` 可以是**部分文档**：读取侧 `parse_platform_settings` 会把
    未给的键补成 schema 默认，故只需给出需要非默认的键（与生产保存路径同一份 schema）。
    """

    async def seed() -> None:
        connection = await asyncpg.connect(_asyncpg_dsn(database_url), timeout=10)
        try:
            await connection.execute(
                "INSERT INTO control.platform_setting (tenant_id, revision, settings_json)"
                " VALUES ($1, $2, $3::jsonb)",
                tenant_id,
                revision,
                json.dumps(settings),
            )
        finally:
            await connection.close()

    _run_in_thread(seed)


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
