#!/usr/bin/env python
"""数据库迁移脚本：执行 alembic 子命令（默认 `upgrade head`）。

- 连接串**只来自 ini**（默认 `migrations/alembic.ini` 的 `sqlalchemy.url`），**不读环境变量**；
  换环境就改 ini，或用 `-c <ini>` 指定另一份（如 `alembic.prod.ini`）。
- 与应用解耦：`migrations/env.py` 自足（`target_metadata = None`），只依赖 alembic + sqlalchemy[asyncio]
  + asyncpg，**不 import 任何业务包**，所以脚本机只需仓库检出 + uv，不需要装 muad-* 包。
- 可跑在：本地 / CI / 脚本机 / 发布流水线。集群内如需 Job，另配载体镜像。

用法：
    uv run python migrations/db_migrate.py                                  # → upgrade head（默认 ini）
    uv run python migrations/db_migrate.py current                          # 透传任意 alembic 子命令
    uv run python migrations/db_migrate.py -c migrations/alembic.prod.ini upgrade head
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from _config import load_dsn, parse_config_args, resolve_ini


def masked(dsn: str) -> str:
    """打印目标库时不泄露口令。"""
    parts = urlsplit(dsn)
    userinfo, at, host = parts.netloc.rpartition("@")
    if not at:
        return dsn
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{user}:***@{host}"))


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    given_ini, args = parse_config_args(argv)
    ini = resolve_ini(given_ini, root)
    args = args or ["upgrade", "head"]
    dsn = load_dsn(ini, "sqlalchemy.url", purpose="迁移需要目标库连接串")

    os.chdir(root)  # ini 的 `script_location = migrations` 与 `prepend_sys_path = .` 都相对仓库根
    print(f"db_migrate: ini={ini} target={masked(dsn)} cmd={' '.join(args)}", file=sys.stderr)

    from alembic.config import main as alembic_main  # 延迟 import：缺依赖时报错更直白

    alembic_main(argv=["-c", str(ini), *args], prog="db_migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
