"""[E-05] workspace_shell 默认没有 session-manager。

真实边界：WorkspaceShellRunner → bwrap → PATH/环境/socket 检查。探针在真实 bwrap
内运行；调用 session-manager 必须得到 command not found/明确错误，而不是平台登录态。
"""

from __future__ import annotations

import pytest

from tests.acceptance.user_skill_sandbox.workspace_shell_support import run_probe

pytestmark = pytest.mark.integration


def test_e05_workspace_shell_has_no_session_manager_path_or_socket() -> None:
    """session-manager 不在 PATH；MUAD_SESSION_SOCKET 不存在；调用返回明确失败。"""
    payload = run_probe("no_session")

    env = payload["env"]
    path_entries = set(env["path"].split(":"))
    assert "/workspace/bin" not in path_entries
    assert env["which"] is None
    assert not any("session-manager" in entry for entry in path_entries)
    assert env["socket"] in (None, "")

    assert payload["call_exit_code"] != 0
    stderr = str(payload["call_stderr"]).lower()
    assert "not found" in stderr or "command not found" in stderr
