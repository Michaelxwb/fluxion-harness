"""[harness-skill#RULE-skill-001] 真实 Artifact Store/READY 缓存 → 沙箱执行。

真实边界：NFS 目录 → SkillArtifactCache（checksum 校验 + READY 原子落盘）→
SandboxedSkillExecutor → bwrap 只读 /skill。不 mock 文件系统或 bwrap。
"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_skill_execution_from_real_ready_cache_keeps_checksum_and_ready() -> None:
    payload = run_probe("cache")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "SUCCEEDED", result
    assert result["result"]["helper"] == 41
    assert result["result"]["output"] == "/workspace/outputs/cache-exec"
    assert payload["ready_is_file"] is True
    assert payload["ready_before"] == payload["ready_after"]
    assert payload["cache_checksum_before"] == payload["cache_checksum_after"]
    assert payload["ready_before"].startswith("artifact_id=probe-artifact\nchecksum=")
