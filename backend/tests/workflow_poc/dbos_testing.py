"""DBOS PoC 测试基础设施（TASK-003）：worker 子进程封装、轮询断言、evidence 汇出。

被 `test_poc_dbos.py` / `test_poc_dbos_baseline.py` 复用；不依赖 dbos_app（测试侧件）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = BACKEND_DIR / "tests" / "workflow_poc" / "evidence" / "dbos.json"

# evidence 汇总（进程内共享；各测试模块 teardown 时落盘）
DBOS_EVIDENCE: dict[str, object] = {
    "candidate": "dbos",
    "library_version": None,
    "criteria": {},
    "baseline": None,
}


def record_evidence(
    criterion: str,
    *,
    passed: bool,
    detail: str,
    metrics: dict[str, object] | None = None,
) -> None:
    """记录单口径结果；TASK-005 矩阵回填的数据源。"""
    criteria = DBOS_EVIDENCE["criteria"]
    assert isinstance(criteria, dict)
    criteria[criterion] = {"passed": passed, "detail": detail, "metrics": metrics or {}}


def write_dbos_evidence() -> None:
    """落盘 evidence JSON（幂等；以最后一次调用时的汇总为准）。"""
    try:
        DBOS_EVIDENCE["library_version"] = metadata.version("dbos")  # type: ignore[assignment]
    except metadata.PackageNotFoundError:
        DBOS_EVIDENCE["library_version"] = "unknown"
    criteria = DBOS_EVIDENCE["criteria"]
    assert isinstance(criteria, dict)
    expected = {"P-CRASH", "P-TIMER", "P-IDEMP", "P-PIN", "P-TIMEOUT", "P-SCALE", "P-SIGNAL"}
    DBOS_EVIDENCE["all_criteria_passed"] = (
        expected.issubset(criteria.keys())
        and all(entry["passed"] for entry in criteria.values() if isinstance(entry, dict))
    )
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(DBOS_EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class WorkerProcess:
    """DBOS worker 子进程封装：后台线程持续读 stdout，支持等标记行 / kill。"""

    def __init__(self, args: list[str], *, extra_env: dict[str, str] | None = None) -> None:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tests.workflow_poc.dbos_worker", *args],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        if self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_for(self, marker: str, *, timeout: float) -> float:
        """等待以 marker 开头的行出现；返回等待秒数。超时/进程退出即失败并附输出。"""
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if any(line.startswith(marker) for line in self.lines):
                return time.monotonic() - started
            returncode = self.proc.poll()
            if returncode is not None:
                time.sleep(0.2)
                if any(line.startswith(marker) for line in self.lines):
                    return time.monotonic() - started
                raise AssertionError(
                    f"worker exited rc={returncode} before '{marker}'; output:\n{self.output}"
                )
            time.sleep(0.05)
        raise AssertionError(
            f"timeout {timeout}s waiting for '{marker}'; output:\n{self.output}"
        )

    def kill(self) -> None:
        """SIGKILL（真实进程崩溃模拟，S-02/RPO）。"""
        self.proc.kill()
        self.proc.wait(timeout=10)

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()

    @property
    def output(self) -> str:
        return "\n".join(self.lines)


def wait_for_condition(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 0.1,
    description: str = "condition",
) -> float:
    """同步轮询直到 predicate 为真（用于子进程 workflow 的 DB 观察）。"""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return time.monotonic() - started
        time.sleep(interval)
    raise AssertionError(f"timeout {timeout}s waiting for {description}")
