"""[E-01] stdout 洪泛有界：父进程内存不随输出增长，子进程被终止/回收（真实 bwrap）。"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_e01_stdout_flood_is_bounded_and_process_tree_reaped() -> None:
    payload = run_probe("e01")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "FAILED"
    assert result["output_notice"] == "SANDBOX_OUTPUT_LIMIT"
    assert payload["stdout_bytes"] <= 64 * 1024
    # 脚本本来会输出 32 MiB：父进程 RSS 只允许有界增长（采集/解码/日志开销）。
    assert payload["rss_delta_kb"] < 32 * 1024, payload
    assert payload["duration_sec"] < 30
    assert payload["orphans"] == [], payload["orphans"]
