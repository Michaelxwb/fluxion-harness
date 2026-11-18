"""Workspace 共享卷预算（TASK-006）：启动设置 → 执行期 policy 的唯一换算点。

ConfigMap 里的 `WORKSPACE_*` 是部署事实；Runtime/Worker 在启动时各取一份快照，
执行路径不重新读环境。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import SharedSettings

#: agent 目录上的草稿活动标记；worker 清理按它的 mtime 给活跃草稿保活。
DRAFT_ACTIVITY_MARKER = ".draft-activity"


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    root: str
    quota_bytes: int
    user_quota_bytes: int
    file_limit: int
    data_ttl_days: int


def workspace_policy_from_settings(settings: SharedSettings) -> WorkspacePolicy:
    # 宿主绝对路径是 SandboxSpec 的前置条件：相对路径在启动边界归一，不流入执行期。
    root = str(Path(settings.workspace_root).expanduser().resolve())
    return WorkspacePolicy(
        root=root,
        quota_bytes=settings.workspace_quota_mib * 1024 * 1024,
        user_quota_bytes=settings.workspace_user_quota_mib * 1024 * 1024,
        file_limit=settings.workspace_file_limit,
        data_ttl_days=settings.workspace_data_ttl_days,
    )
