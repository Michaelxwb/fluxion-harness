"""Workspace 共享卷预算（TASK-006）：启动设置 → 执行期 policy 的唯一换算点。

ConfigMap 里的 `WORKSPACE_*` 是部署事实；Runtime/Worker 在启动时各取一份快照，
执行路径不重新读环境。
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import SharedSettings


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    root: str
    quota_bytes: int
    user_quota_bytes: int
    file_limit: int
    output_ttl_days: int
    data_ttl_days: int


def workspace_policy_from_settings(settings: SharedSettings) -> WorkspacePolicy:
    return WorkspacePolicy(
        root=settings.workspace_root,
        quota_bytes=settings.workspace_quota_mib * 1024 * 1024,
        user_quota_bytes=settings.workspace_user_quota_mib * 1024 * 1024,
        file_limit=settings.workspace_file_limit,
        output_ttl_days=settings.workspace_output_ttl_days,
        data_ttl_days=settings.workspace_data_ttl_days,
    )
