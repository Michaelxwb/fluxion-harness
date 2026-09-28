"""[S-01] DFX 分层基线：四类 marker 已登记，`-m unit` 能在无 DB / 无网络下收集并通过。

真实边界：pytest marker 登记与分层选择器行为（纯逻辑，无 IO）。
不覆盖（design §2.3 技术债①）：全仓既有用例未逐条打 marker，`-m unit` 的选择范围只覆盖已登记
marker 的用例；本任务不改全仓用例、不改 `Makefile` 既有目标语义。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "pyproject.toml"
DFX_DIR = Path(__file__).resolve().parent
REQUIRED_MARKERS = ("unit", "contract", "integration", "e2e")
# 子进程复用本文件时用它掐断递归：子进程内本用例只作为「被选中的本层用例」，不再向下探测。
PROBE_ENV = "DFX_LAYER_PROBE"


def _registered_markers() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return [str(entry).split(":")[0].strip() for entry in options.get("markers", [])]


def _probe_env() -> dict[str, str]:
    """探测子进程的环境：显式移除 DB/Redis 配置，使「单元层不依赖 IO」可被证伪。"""
    env = dict(os.environ)
    env[PROBE_ENV] = "1"
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    return env


def _run_pytest(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """用当前解释器跑子进程：不依赖 `uv` 在 PATH 上（非交互 shell 里它常不在）。"""
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.unit
def test_s01_unit_layer_is_selectable_and_io_free() -> None:
    """marker 齐备 ⇒ `-m unit` 可收集、可运行，且在无 `DATABASE_URL`/`REDIS_URL` 时全绿。"""
    if os.environ.get(PROBE_ENV) == "1":
        return

    registered = _registered_markers()
    missing = [name for name in REQUIRED_MARKERS if name not in registered]
    assert not missing, f"pyproject.toml 未登记分层 marker：{missing}"

    env = _probe_env()
    collect = _run_pytest("-m", "unit", "--collect-only", "-q", str(DFX_DIR), env=env)
    assert collect.returncode == 0, f"`-m unit` 收集失败：{collect.stdout[-500:]}"
    assert "PytestUnknownMarkWarning" not in (collect.stdout + collect.stderr), (
        "marker 未登记会触发 unknown-mark 警告"
    )
    selected = [line for line in collect.stdout.splitlines() if "::" in line]
    assert selected, "`-m unit` 未选中任何用例（marker 未生效）"

    run = _run_pytest("-m", "unit", "-q", str(DFX_DIR), env=env)
    assert run.returncode == 0, f"单元层在无 DATABASE_URL/REDIS_URL 下未全绿：{run.stdout[-800:]}"
