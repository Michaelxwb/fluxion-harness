#!/usr/bin/env python
"""独立数据库迁移脚本：与应用启动流程、业务代码完全解耦（不 import 任何业务包）。

- 依赖来自根 `uv.lock`：`uv run python migrations/db_migrate.py`（alembic + sqlalchemy[asyncio] + asyncpg）。
- `migrations/env.py` 自足：`target_metadata = None`、连接串取 `DATABASE_URL` 环境变量，不依赖 app 模块。
- 缺 `DATABASE_URL` 直接失败（exit 2）——否则 env.py 会回落到 alembic.ini 里的本地开发串，静默误连 localhost。
- 可跑在：本地、CI、脚本机/发布流水线（任何有仓库检出 + uv 的地方）。集群内如需 Job，另配载体镜像。

用法：
    DATABASE_URL=<dsn> uv run python migrations/db_migrate.py            # → upgrade head
    DATABASE_URL=<dsn> uv run python migrations/db_migrate.py current    # 透传任意 alembic 子命令
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def masked(dsn: str) -> str:
    """打印目标库时不泄露口令。"""
    parts = urlsplit(dsn)
    userinfo, at, host = parts.netloc.rpartition("@")
    if not at:
        return dsn
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{user}:***@{host}"))


def main(argv: list[str]) -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required (e.g. postgresql+asyncpg://user@host:5432/muad)", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)  # alembic.ini 的 `script_location = migrations` 与 `prepend_sys_path = .` 都相对仓库根
    args = argv or ["upgrade", "head"]
    print(f"db_migrate: target={masked(dsn)} cmd={' '.join(args)}", file=sys.stderr)

    from alembic.config import main as alembic_main  # 延迟 import：缺依赖时报错更直白

    alembic_main(argv=["-c", "migrations/alembic.ini", *args], prog="db_migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
