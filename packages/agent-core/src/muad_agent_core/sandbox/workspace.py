"""Workspace 路径、租约与目录契约：Runtime/Worker 共用的唯一实现。

把启动设置变成可校验的目录布局（tenant/user/agent），并把「元数据 acquire/release」
与「PVC 目录创建」收口在 ``WorkspaceStore``。数据库访问通过 ``WorkspaceMetadataStore``
协议注入，本模块保持 stdlib-only，便于在真实 bwrap 探针/容器内复用。

约定：

- 只创建并挂载**当前 user + Agent** 目录；workspace 根（PVC 根）永远不进入沙箱。
- ``data/`` 跨执行持久；``outputs/{execution_id}/`` 每次执行独立。
- 沙箱内固定看到 ``/workspace/data`` 与 ``/workspace/outputs/{execution_id}``；
  空 root / 空路径必须在装配处显式失败，绝不回落到 cwd 或根目录。
- 输出文件由生产者先写临时文件再 ``os.replace``，避免执行中途被杀时留下半文件。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

MIB = 1024 * 1024
DAY_SECONDS = 24 * 60 * 60
WORKSPACE_MOUNT = "/workspace"
WORKSPACE_DATA_MOUNT = f"{WORKSPACE_MOUNT}/data"
WORKSPACE_STATE_ACTIVE = "ACTIVE"

logger = logging.getLogger("muad_agent_core.sandbox.workspace")


class WorkspaceSettings(Protocol):
    workspace_root: str
    workspace_quota_mib: int
    workspace_user_quota_mib: int
    workspace_file_limit: int
    workspace_output_ttl_days: int
    workspace_data_ttl_days: int


class WorkspaceError(RuntimeError):
    """Workspace 元数据/目录不可用；error code 走既有 ``WORKSPACE_*`` 契约。"""

    code = "WORKSPACE_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class WorkspaceUnavailable(WorkspaceError):
    code = "WORKSPACE_UNAVAILABLE"


class WorkspaceDeleting(WorkspaceError):
    code = "WORKSPACE_DELETING"


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

    def __post_init__(self) -> None:
        text = str(self.root).strip()
        if not text or text in {".", "/"}:
            raise ValueError(
                "workspace root must be an explicit directory; refusing to use the "
                "current directory or filesystem root"
            )

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
    root = str(settings.workspace_root).strip()
    if not root:
        raise ValueError("workspace_root must not be empty")
    return WorkspaceLayout(
        root=Path(root),
        quota_bytes=settings.workspace_quota_mib * MIB,
        user_quota_bytes=settings.workspace_user_quota_mib * MIB,
        file_limit=settings.workspace_file_limit,
        output_ttl_seconds=settings.workspace_output_ttl_days * DAY_SECONDS,
        data_ttl_seconds=settings.workspace_data_ttl_days * DAY_SECONDS,
    )


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseRecord:
    """数据库中的活跃租约（不含路径；路径由 ``WorkspaceStore`` 补齐）。"""

    id: uuid.UUID
    workspace_id: uuid.UUID
    execution_id: str
    lease_until: datetime


class WorkspaceMetadataStore(Protocol):
    """``task.workspace`` / ``task.workspace_lease`` 的最小读写协议。

    实现方必须保证：

    - 同一 tenant+user+agent 只复用一条活跃 workspace；
    - 同一 workspace 的 execution_id 租约幂等更新，不产生重复活跃行；
    - workspace 非 ACTIVE（DELETING/DELETED）时拒绝执行，抛
      ``WorkspaceDeleting``，绝不自动恢复。
    """

    async def acquire(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        execution_id: str,
        run_id: str,
        task_id: str,
        lease_sec: float,
    ) -> WorkspaceLeaseRecord: ...

    async def release(self, lease_id: uuid.UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    """一次执行的 workspace 句柄：目录、租约和沙箱内可见路径。"""

    id: uuid.UUID
    workspace_id: uuid.UUID
    execution_id: str
    lease_until: datetime
    workspace_dir: Path
    data_dir: Path
    output_dir: Path
    data_mount: str
    output_mount: str
    workspace_mount: str


class WorkspaceStore:
    """把元数据租约与 PVC 目录绑定在一起；不碰 artifacts/PVC 根之外的目录。"""

    def __init__(
        self,
        *,
        layout: WorkspaceLayout,
        metadata: WorkspaceMetadataStore,
        sandbox_mount: str | None = WORKSPACE_MOUNT,
    ) -> None:
        self._layout = layout
        self._metadata = metadata
        self._mount = sandbox_mount

    async def acquire(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        execution_id: str,
        run_id: str = "",
        task_id: str = "",
        lease_sec: float = 300.0,
    ) -> WorkspaceLease:
        if lease_sec <= 0:
            raise WorkspaceUnavailable("workspace lease_sec must be positive")
        try:
            workspace_dir = self._layout.workspace_dir(
                tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
            )
            data_dir = self._layout.data_dir(
                tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
            )
            output_dir = self._layout.output_dir(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                execution_id=execution_id,
            )
        except ValueError as exc:
            raise WorkspaceUnavailable(str(exc)) from exc
        record = await self._metadata.acquire(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            execution_id=execution_id,
            run_id=run_id,
            task_id=task_id,
            lease_sec=lease_sec,
        )
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            await self._release_quietly(record.id)
            raise WorkspaceUnavailable(f"workspace directory is unavailable: {exc}") from exc

        workspace_mount = self._mount or str(workspace_dir)
        if self._mount is None:
            data_mount, output_mount = str(data_dir), str(output_dir)
        else:
            base = self._mount.rstrip("/") or "/"
            data_mount = f"{base}/data"
            output_mount = f"{base}/outputs/{_safe_component(execution_id, 'execution_id')}"
        return WorkspaceLease(
            id=record.id,
            workspace_id=record.workspace_id,
            execution_id=execution_id,
            lease_until=record.lease_until,
            workspace_dir=workspace_dir,
            data_dir=data_dir,
            output_dir=output_dir,
            data_mount=data_mount,
            output_mount=output_mount,
            workspace_mount=workspace_mount,
        )

    async def release(self, lease: WorkspaceLease) -> None:
        await self._metadata.release(lease.id)

    async def _release_quietly(self, lease_id: uuid.UUID) -> None:
        try:
            await self._metadata.release(lease_id)
        except Exception as exc:  # noqa: BLE001 - 目录失败优先，租约残留需可见
            logger.warning(
                "workspace_lease_release_failed lease_id=%s error=%s", lease_id, type(exc).__name__
            )


__all__ = [
    "DAY_SECONDS",
    "MIB",
    "WORKSPACE_DATA_MOUNT",
    "WORKSPACE_MOUNT",
    "WORKSPACE_STATE_ACTIVE",
    "WorkspaceDeleting",
    "WorkspaceError",
    "WorkspaceLayout",
    "WorkspaceLease",
    "WorkspaceLeaseRecord",
    "WorkspaceMetadataStore",
    "WorkspaceSettings",
    "WorkspaceStore",
    "WorkspaceUnavailable",
    "workspace_layout_from_settings",
]
