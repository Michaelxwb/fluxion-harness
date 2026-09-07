#!/usr/bin/env python3
"""初始化 Fluxion 域数据库（建库 + 建表，PostgreSQL，幂等，ADR-A007）。

服务进程（`fluxion serve` / `fluxion-workflow-worker`）启动时**不**建表——
schema 由本脚本负责初始化。已移除 alembic，本脚本是 Fluxion 域 schema 的唯一
初始化入口：复用 `fluxion.registry.schema.metadata`，覆盖全部域表（含
workflow_run / artifact_metadata / secret_credentials / trace_records /
approval_records / eval_runs 等）。

用法：
    # PostgreSQL（本地 / 生产；库不存在自动创建，同实例 postgres 维护库需可连）
    python3 scripts/init_db.py --dsn "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion"

    # 缺省：读环境变量 FLUXION_DATABASE_URL；再缺省本地 PG fluxion 库

幂等：库已存在则跳过创建；metadata.create_all（checkfirst）只建缺失表，
不删已有数据、不改既有表结构。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 让脚本可从仓库任意位置运行：把 backend/src 加入 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from fluxion.registry.schema import metadata  # noqa: E402

_DEFAULT_PG_DSN = "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion"
_MAINTENANCE_DB = "postgres"


def _split_dsn(dsn: str) -> tuple[str, str]:
    """拆 DSN 为（库名，管理 DSN）。管理 DSN 指向同实例的 postgres 维护库，用于建库。"""
    from sqlalchemy.engine.url import make_url

    url = make_url(dsn)
    if not url.database:
        raise ValueError(f"DSN 缺少数据库名：{dsn}")
    # 注意：str(url) 会把密码打码成 ***，必须用 hide_password=False 渲染。
    admin = url.set(database=_MAINTENANCE_DB).render_as_string(hide_password=False)
    return url.database, admin


async def _ensure_database(admin_dsn: str, db_name: str) -> None:
    """建库（幂等，已存在则跳过）。CREATE DATABASE 不能在事务块内跑。"""
    import asyncpg

    # asyncpg 直连管理库，autocommit 建库
    raw = admin_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(raw)
    try:
        exists = await conn.fetchval("select 1 from pg_database where datname=$1", db_name)
        if exists:
            print(f"[OK] 数据库已存在：{db_name}（跳过创建）")
            return
        await conn.execute(f'create database "{db_name}"')
        print(f"[OK] 数据库已创建：{db_name}")
    finally:
        await conn.close()


async def _init(dsn: str) -> None:
    db_name, admin_dsn = _split_dsn(dsn)
    await _ensure_database(admin_dsn, db_name)
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        print(f"[OK] 表初始化完成：{dsn}")
        print(f"     共 {len(metadata.tables)} 张表（幂等，已存在的不重建）")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 Fluxion 域数据库表（PostgreSQL）")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("FLUXION_DATABASE_URL", _DEFAULT_PG_DSN),
        help="数据库 DSN（缺省读 FLUXION_DATABASE_URL，再缺省本地 PG fluxion 库）",
    )
    args = parser.parse_args()
    asyncio.run(_init(args.dsn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
