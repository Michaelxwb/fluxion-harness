#!/usr/bin/env python3
"""初始化 Fluxion 域数据库表（PostgreSQL / SQLite 双库，幂等）。

服务进程（`fluxion serve` / `fluxion-workflow-worker`）启动时**不**建表——
schema 由本脚本负责初始化。已移除 alembic，本脚本是 Fluxion 域 schema 的唯一
初始化入口：复用 `fluxion.registry.schema.metadata`，覆盖全部域表（含
workflow_run / artifact_metadata / secret_credentials / trace_records /
approval_records / eval_runs 等）。

用法：
    # SQLite（dev）
    python3 scripts/init_db.py --dsn "sqlite+aiosqlite:///./fluxion-dev.db"

    # PostgreSQL（本地 / 生产；数据库本身需已存在，CREATE DATABASE 属 DBA 操作）
    python3 scripts/init_db.py --dsn "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion"

    # 缺省：读环境变量 FLUXION_DATABASE_URL；再缺省 SQLite 文件 fluxion-dev.db

幂等：metadata.create_all（checkfirst）只建缺失表，不删已有数据、不改既有表结构。
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

_DEFAULT_SQLITE_DSN = "sqlite+aiosqlite:///./fluxion-dev.db"


async def _init(dsn: str) -> None:
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        print(f"[OK] 表初始化完成：{dsn}")
        print(f"     共 {len(metadata.tables)} 张表（幂等，已存在的不重建）")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 Fluxion 域数据库表（PG/SQLite）")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("FLUXION_DATABASE_URL", _DEFAULT_SQLITE_DSN),
        help="数据库 DSN（缺省读 FLUXION_DATABASE_URL，再缺省 SQLite 文件）",
    )
    args = parser.parse_args()
    asyncio.run(_init(args.dsn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
