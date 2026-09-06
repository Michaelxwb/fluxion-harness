"""E-02 runner 半边：无 PG 时直接失败，不自拉容器、不回落（ADR-A007）。

真实边界：真实 runner 子进程 + 真实（不可达）PG DSN。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_registry_contract_tests.py"

_UNREACHABLE = "postgresql+asyncpg://mmuser:mmuser@127.0.0.1:1/fluxion_test"


def test_runner_fails_fast_without_postgres() -> None:
    env = dict(os.environ)
    env["FLUXION_POSTGRES_DSN"] = _UNREACHABLE
    env.pop("FLUXION_REQUIRE_POSTGRES_CONTRACT", None)
    proc = subprocess.run(
        [sys.executable, str(RUNNER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0, f"runner 必须非零退出：{proc.stdout[-500:]}"
    combined = proc.stdout + proc.stderr
    # 老行为（自拉容器跑通 exit 0 / 回落分支）在此 DSN 下必为 exit 0；
    # 报错必须指明 PG 连接问题。
    assert "FLUXION_POSTGRES_DSN" in combined or "PostgreSQL" in combined or "postgres" in combined.lower(), (
        f"报错必须指明 PG 连接问题：{combined[-500:]}"
    )
