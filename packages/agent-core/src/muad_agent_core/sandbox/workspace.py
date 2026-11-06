"""Workspace 路径与预算投影：把启动设置变成可校验的目录布局。

真正的 lease/配额统计/清理循环属于 WorkspaceStore（后续任务）；这里只提供
Runtime/Worker 共用的**路径与上限事实**，避免每个调用方各自拼 `/mnt/...`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MIB = 1024 * 1024
DAY_SECONDS = 24 * 60 * 60


class WorkspaceSettings(Protocol):
    workspace_root: str
    workspace_quota_mib: int
    workspace_user_quota_mib: int
    workspace_file_limit: int
    workspace_output_ttl_days: int
    workspace_data_ttl_days: int


def _safe_component(value: str, label: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or "/" in text or "\\" in text or "\x00" in text:
        raise ValueError(f"invalid workspace {label}: {value!r}")
    return text


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    root: Path
    quota_bytes: int
    user_quota_bytes: int
    file_limit: int
    output_ttl_seconds: int
    data_ttl_seconds: int

    def workspace_dir(self, *, tenant_id: str, user_id: str, agent_id: str) -> Path:
        return (
            self.root
            / _safe_component(tenant_id, "tenant_id")
            / _safe_component(user_id, "user_id")
            / _safe_component(agent_id, "agent_id")
        )

    def data_dir(self, *, tenant_id: str, user_id: str, agent_id: str) -> Path:
        return self.workspace_dir(
            tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
        ) / "data"

    def output_dir(
        self, *, tenant_id: str, user_id: str, agent_id: str, execution_id: str
    ) -> Path:
        return self.workspace_dir(
            tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
        ) / "outputs" / _safe_component(execution_id, "execution_id")


def workspace_layout_from_settings(settings: WorkspaceSettings) -> WorkspaceLayout:
    """读取 ``WORKSPACE_*`` 启动项；单位换算只在装配处做一次。"""
    return WorkspaceLayout(
        root=Path(settings.workspace_root),
        quota_bytes=settings.workspace_quota_mib * MIB,
        user_quota_bytes=settings.workspace_user_quota_mib * MIB,
        file_limit=settings.workspace_file_limit,
        output_ttl_seconds=settings.workspace_output_ttl_days * DAY_SECONDS,
        data_ttl_seconds=settings.workspace_data_ttl_days * DAY_SECONDS,
    )


__all__ = [
    "DAY_SECONDS",
    "MIB",
    "WorkspaceLayout",
    "WorkspaceSettings",
    "workspace_layout_from_settings",
]
