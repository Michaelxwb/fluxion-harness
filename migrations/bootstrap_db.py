#!/usr/bin/env python
"""一次性数据库引导：建角色 + 建库 + 授权（PG 级，与 schema 迁移分开）。

- 连接串**只来自 ini**（默认 `migrations/alembic.ini`，**不读环境变量**）：
  `sqlalchemy.url` = 目标库（应用角色），`admin_database_url` = PG 管理员（指向维护库）。
  换环境就改 ini，或用 `-c <另一份 ini>`；管理员串也可用 `--admin-url <dsn>` 临时覆盖。
- **幂等**：角色与库已存在则不动（`--reset-password` 显式改口令）；`--check` 只报告不修改。
- 职责边界：本脚本只建 **database 与 role**；schema/表/索引归 `migrations/db_migrate.py`，
  首个 **Console 管理员账号**（应用层账号行，用于登录 Console）归 Console CLI：
  `uv run muad-console-admin create-admin …`。
- 用同步 psycopg（根依赖自带、类型齐全、`autocommit` 天然适配 `CREATE DATABASE`——它不能在事务块里跑）。
  口令不落终端/日志（输出一律打码），DDL 的标识符/字面量交给服务端 `format(%I/%L)` 转义。

用法：
    uv run python migrations/bootstrap_db.py [--check | --reset-password]
    uv run python migrations/bootstrap_db.py -c migrations/alembic.prod.ini
    uv run python migrations/bootstrap_db.py --admin-url postgresql://postgres@host:5432/postgres
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from _config import load_dsn, parse_config_args, resolve_ini


def masked(dsn: str) -> str:
    parts = urlsplit(dsn)
    userinfo, at, host = parts.netloc.rpartition("@")
    if not at:
        return dsn
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{user}:***@{host}"))


def split_target(dsn: str) -> tuple[str, str | None, str]:
    """→ (role, password, database)；同时校验 DSN 形态。"""
    parts = urlsplit(dsn)
    userinfo, at, _host = parts.netloc.rpartition("@")
    if not at:
        raise SystemExit("DATABASE_URL 缺少用户名/主机（形如 postgresql+asyncpg://user:pw@host:5432/db）")
    user, _, password = userinfo.partition(":")
    return user, (password or None), (parts.path.lstrip("/") or user)


def server_sql(cur: psycopg.Cursor[tuple[str]], template: str, *args: object) -> str:
    """用服务端 format() 生成 DDL，参数显式 ::text 让类型可推断。

    template 里的 `%` 必须写成 `%%`——psycopg 会对整条 SQL 做占位符解析，
    否则 `%I`/`%L` 会被它当作占位符并报 `ProgrammingError: only '%s', '%b', '%t' are allowed`。
    """
    escaped = template.replace("%", "%%")
    placeholders = ", ".join("%s::text" for _ in args)
    cur.execute(f"SELECT format('{escaped}', {placeholders})", args)
    row = cur.fetchone()
    assert row is not None
    return row[0]


def run(admin_dsn: str, target_dsn: str, *, check: bool, reset_password: bool) -> int:
    role, password, database = split_target(target_dsn)
    print(
        f"bootstrap: admin={masked(admin_dsn)} target={masked(target_dsn)} (role={role} db={database})",
        file=sys.stderr,
    )

    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        role_exists = cur.fetchone() is not None
        cur.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (database,))
        row = cur.fetchone()
        owner = row[0] if row else None
        print(f"  role {role}: {'exists' if role_exists else 'missing'}")
        print(f"  database {database}: {f'owner={owner}' if owner else 'missing'}")
        if check:
            return 0

        if not role_exists:
            if not password:
                print("创建角色需要口令：请在 DATABASE_URL 里带上口令，或先人工建好角色", file=sys.stderr)
                return 2
            cur.execute(server_sql(cur, "CREATE ROLE %I LOGIN PASSWORD %L", role, password))
            print(f"  created role {role}")
        elif reset_password:
            if not password:
                print("--reset-password 需要 DATABASE_URL 里的口令", file=sys.stderr)
                return 2
            cur.execute(server_sql(cur, "ALTER ROLE %I LOGIN PASSWORD %L", role, password))
            print(f"  reset password for role {role}")

        if owner is None:
            cur.execute(server_sql(cur, "CREATE DATABASE %I OWNER %I", database, role))
            print(f"  created database {database} (owner={role})")
        elif owner != role:
            print(
                f"  database {database} 已存在但属主是 {owner}（非 {role}）——未改动，请人工确认",
                file=sys.stderr,
            )
            return 1

        cur.execute(server_sql(cur, "GRANT ALL ON DATABASE %I TO %I", database, role))
        print(f"  granted ALL on {database} to {role}")

    print(
        f"bootstrap: done  →  下一步：DATABASE_URL={masked(target_dsn)} "
        "uv run python migrations/db_migrate.py",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    given_ini, args = parse_config_args(argv)
    ini = resolve_ini(given_ini, root)

    admin_override: str | None = None
    rest: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == "--admin-url":
            if index + 1 >= len(args):
                raise SystemExit("--admin-url 需要一个连接串参数")
            admin_override = args[index + 1]
            index += 2
            continue
        rest.append(args[index])
        index += 1

    unknown = [arg for arg in rest if arg not in ("--check", "--reset-password")]
    if unknown:
        print(
            f"未知参数：{unknown}（支持 --check / --reset-password / --admin-url <dsn> / -c <ini>）",
            file=sys.stderr,
        )
        return 2

    target_dsn = load_dsn(ini, "sqlalchemy.url", purpose="引导脚本需要目标库连接串")
    admin_dsn = admin_override or load_dsn(
        ini, "admin_database_url", purpose="需要 PG 管理员连接串（也可用 --admin-url 覆盖）"
    )
    print(f"bootstrap: ini={ini}", file=sys.stderr)
    return run(admin_dsn, target_dsn, check="--check" in rest, reset_password="--reset-password" in rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
