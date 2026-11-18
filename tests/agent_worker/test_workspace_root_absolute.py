"""WORKSPACE_ROOT 启动归一：相对路径不得进入 SandboxSpec。"""

from __future__ import annotations

from pathlib import Path

from muad_common.settings import SharedSettings
from muad_common.workspaces import workspace_policy_from_settings


def test_relative_workspace_root_is_normalized_to_absolute() -> None:
    policy = workspace_policy_from_settings(SharedSettings(workspace_root="./.data/workspaces"))
    assert Path(policy.root).is_absolute()
    assert Path(policy.root).name == "workspaces"


def test_absolute_workspace_root_is_preserved() -> None:
    policy = workspace_policy_from_settings(SharedSettings(workspace_root="/mnt/muad-workspaces"))
    assert policy.root == "/mnt/muad-workspaces"
