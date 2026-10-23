"""Global pytest hooks.

只做一件事：用例失败/出错时立刻输出 `FAILED <nodeid>`，并可选追加到
`PYTEST_FAILURE_LOG` 指向的文件。

为什么需要：`pytest -q` 的失败清单只在**整轮结束的 terminal summary** 里；验收/单测被
timeout 或外部 kill 时摘要根本不会写，日志里只剩一串 `F`，事后只能靠点号位置反推用例——
2026-10-24 全量单测被掐断后即出现这种情况。这里在 `pytest_runtest_logreport` 里即时输出，
保证即使进程被杀也留下可定位的用例名。
"""

from __future__ import annotations

import os

import pytest


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not report.failed:
        return
    line = f"FAILED {report.nodeid} [{report.when}] ({report.duration:.2f}s)"
    print(line, flush=True)
    path = os.environ.get("PYTEST_FAILURE_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # 日志写入失败不能反过来影响测试结果；stdout 已经留下了同一行。
        pass
