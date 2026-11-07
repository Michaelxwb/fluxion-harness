"""Workspace 预算换算：启动设置是唯一来源，MiB → 字节只在这里发生。"""

from __future__ import annotations

from muad_common import SharedSettings, workspace_policy_from_settings


def test_workspace_policy_converts_mib_and_keeps_limits() -> None:
    settings = SharedSettings(
        _env_file=None,
        workspace_root="/tmp/ws-test",
        workspace_quota_mib=7,
        workspace_user_quota_mib=9,
        workspace_file_limit=11,
        workspace_output_ttl_days=13,
        workspace_data_ttl_days=17,
    )

    policy = workspace_policy_from_settings(settings)

    assert policy.root == "/tmp/ws-test"
    assert policy.quota_bytes == 7 * 1024 * 1024
    assert policy.user_quota_bytes == 9 * 1024 * 1024
    assert policy.file_limit == 11
    assert policy.output_ttl_days == 13
    assert policy.data_ttl_days == 17
