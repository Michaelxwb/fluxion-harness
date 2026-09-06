#!/usr/bin/env python3
"""Registry Contract 测试启动器（ADR-A007 PG-Only）。

直连本地 PostgreSQL（`FLUXION_POSTGRES_DSN`，默认本地 mmuser `fluxion_test` 库），
跑 `backend/tests/contract/test_registry_store.py`。无 PG 时直接失败退出，
不自拉容器、不回落其他后端。CI 由 postgres 服务容器提供 PG。
"""

from __future__ import annotations

import os
import subprocess
import sys

_DEFAULT_DSN = "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test"


def main() -> int:
    dsn = os.environ.get("FLUXION_POSTGRES_DSN", _DEFAULT_DSN)
    env = os.environ.copy()
    env["FLUXION_POSTGRES_DSN"] = dsn
    env["FLUXION_REQUIRE_POSTGRES_CONTRACT"] = "1"
    print(f"[registry-contract] PostgreSQL Contract 测试：{dsn}")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests/contract/test_registry_store.py", "-q"],
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        print(
            "[registry-contract] 失败：请确认本地 PG 已启动且 "
            "FLUXION_POSTGRES_DSN 可达（ADR-A007：不自拉容器、不回落）。",
            file=sys.stderr,
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
