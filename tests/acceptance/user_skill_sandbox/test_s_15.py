"""[S-15] 只读 /skill、并发执行隔离与 READY 缓存不变（真实 bwrap）。"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_s15_readonly_package_concurrent_workspaces_and_ready_unchanged() -> None:
    payload = run_probe("s15")

    assert payload["ok"] is True, payload
    first = payload["first"]
    second = payload["second"]
    assert first["status"] == "SUCCEEDED", first
    assert second["status"] == "SUCCEEDED", second
    assert first["result"]["skill_readable"] is True
    assert second["result"]["skill_readable"] is True
    # 只读挂载：写 /skill 必须失败
    assert first["result"]["write_error"] == "OSError"
    assert second["result"]["write_error"] == "OSError"
    assert first["result_file"] == "/workspace/outputs/exec-a"
    assert second["result_file"] == "/workspace/outputs/exec-b"
    # 两个并发执行各自只看到自己的 outputs 目录
    assert payload["first_workspace_only_a"] is True
    assert payload["second_workspace_only_b"] is True
    # 共享 READY 缓存不被执行器改写
    assert payload["ready_unchanged"] is True
    assert payload["ready_sentinel"] == "artifact_id=probe\nchecksum=sha256:deadbeef\n"
    assert payload["package_checksum_before"] == payload["package_checksum_after"]
