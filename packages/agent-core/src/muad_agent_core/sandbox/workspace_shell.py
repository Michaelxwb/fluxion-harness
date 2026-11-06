"""``workspace_shell`` 执行器：租约 → 目录注入 → 真实沙箱 → 输出扫描。

设计定位（§3.2 执行层）：所有一次性 Shell 命令都走同一个 ``WorkspaceShellRunner``，
只把当前 user + Agent 的 workspace 绑定到 ``/workspace``；不注入 session-manager，
不绑定 Skill 包。``SKILL_DATA_DIR``/``SKILL_OUTPUT_DIR`` 由本模块统一计算，调用方
（Runtime 工具）不能从模型参数里覆盖。

真实边界：WorkspaceStore（元数据/租约）→ 当前目录 → SandboxRunner（bwrap/宿主）
→ stdout/stderr/exit code。任何 release 失败只记录 warning，不吞掉执行结果。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .runner import SandboxRunner
from .spec import SANDBOX_OUTPUT_LIMIT_BYTES, SandboxPolicy, SandboxSpec
from .workspace import WORKSPACE_MOUNT, WorkspaceLease, WorkspaceStore

logger = logging.getLogger("muad_agent_core.sandbox.workspace")

DEFAULT_WORKSPACE_LEASE_SEC = 300.0
DEFAULT_CHANGED_FILE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class WorkspaceShellRequest:
    tenant_id: str
    user_id: str
    agent_id: str
    execution_id: str
    command: str | tuple[str, ...]
    cwd: str = WORKSPACE_MOUNT
    timeout_sec: float = 60.0
    env: Mapping[str, str] = field(default_factory=dict)
    run_id: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceShellResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    changed_files: tuple[str, ...]
    output_dir: str
    execution_id: str


class WorkspaceShellRunner:
    """在 user + Agent workspace 内执行一次性命令（无 Skill 包、无 session）。"""

    def __init__(
        self,
        *,
        store: WorkspaceStore,
        runner_factory: Callable[[], SandboxRunner],
        policy: SandboxPolicy,
        lease_sec: float = DEFAULT_WORKSPACE_LEASE_SEC,
        sandbox_mount: str | None = WORKSPACE_MOUNT,
        output_limit_bytes: int = SANDBOX_OUTPUT_LIMIT_BYTES,
        changed_file_limit: int = DEFAULT_CHANGED_FILE_LIMIT,
    ) -> None:
        if lease_sec <= 0:
            raise ValueError("lease_sec must be positive")
        if output_limit_bytes <= 0 or changed_file_limit <= 0:
            raise ValueError("output limits must be positive")
        self._store = store
        self._runner_factory = runner_factory
        self._policy = policy
        self._lease_sec = lease_sec
        self._sandbox_mount = sandbox_mount
        self._output_limit_bytes = output_limit_bytes
        self._changed_file_limit = changed_file_limit

    async def run(self, request: WorkspaceShellRequest) -> WorkspaceShellResult:
        command = _command_tuple(request.command)
        if request.timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        self._validate_cwd(request.cwd)
        lease = await self._store.acquire(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
            execution_id=request.execution_id,
            run_id=request.run_id,
            task_id=request.task_id,
            lease_sec=max(self._lease_sec, request.timeout_sec + 30.0),
        )
        try:
            cwd = self._resolve_cwd(request.cwd, lease)
            env = {
                **{str(key): str(value) for key, value in request.env.items()},
                "SKILL_DATA_DIR": lease.data_mount,
                "SKILL_OUTPUT_DIR": lease.output_mount,
            }
            spec = SandboxSpec(
                policy=self._policy,
                command=command,
                workspace_dir=lease.workspace_dir,
                env=env,
                timeout_sec=request.timeout_sec,
                output_limit_bytes=self._output_limit_bytes,
                cwd=cwd,
                name="workspace_shell",
            )
            result = await self._runner_factory().run(spec)
            changed = _scan_changed_files(lease.output_dir, self._changed_file_limit)
            return WorkspaceShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                changed_files=changed,
                output_dir=lease.output_mount,
                execution_id=request.execution_id,
            )
        finally:
            try:
                await self._store.refresh_usage(lease)
            except Exception as exc:  # noqa: BLE001 - 统计失败不得覆盖执行结果
                logger.warning(
                    "workspace_shell_usage_refresh_failed execution_id=%s error=%s",
                    request.execution_id,
                    type(exc).__name__,
                )
            try:
                await self._store.release(lease)
            except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖执行结果
                logger.warning(
                    "workspace_shell_release_failed execution_id=%s error=%s",
                    request.execution_id,
                    type(exc).__name__,
                )

    def _validate_cwd(self, cwd: str) -> None:
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            raise ValueError(f"cwd must be an absolute sandbox path: {cwd!r}")
        pure = PurePosixPath(cwd)
        if ".." in pure.parts or "\x00" in cwd:
            raise ValueError(f"cwd escapes the workspace: {cwd!r}")
        if self._sandbox_mount is None:
            if cwd != WORKSPACE_MOUNT and not cwd.startswith(WORKSPACE_MOUNT + "/"):
                raise ValueError(f"cwd must stay under {WORKSPACE_MOUNT}: {cwd!r}")
            return
        mount = self._sandbox_mount.rstrip("/") or "/"
        if cwd != mount and not cwd.startswith(mount + "/"):
            raise ValueError(f"cwd must stay under {mount}: {cwd!r}")

    def _resolve_cwd(self, cwd: str, lease: WorkspaceLease) -> str:
        self._validate_cwd(cwd)
        if self._sandbox_mount is None:
            if cwd == WORKSPACE_MOUNT:
                return str(lease.workspace_dir)
            return str(lease.workspace_dir / cwd[len(WORKSPACE_MOUNT) + 1 :])
        return cwd


def _command_tuple(command: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(command, str):
        if not command.strip():
            raise ValueError("command must be a non-empty string")
        return ("sh", "-c", command)
    items = tuple(command)
    if not items or any(not isinstance(part, str) or not part or "\x00" in part for part in items):
        raise ValueError("command must be a non-empty argv tuple")
    return items


def _scan_changed_files(root: Path, limit: int) -> tuple[str, ...]:
    """只登记 ``outputs/{execution_id}`` 内的普通文件，不跟随 symlink。"""
    if not root.is_dir():
        return ()
    found: list[str] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if not os.path.islink(os.path.join(directory, name))
        )
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path.relative_to(root).as_posix())
            if len(found) >= limit:
                return tuple(found)
    return tuple(found)


__all__ = [
    "DEFAULT_CHANGED_FILE_LIMIT",
    "DEFAULT_WORKSPACE_LEASE_SEC",
    "WorkspaceShellRequest",
    "WorkspaceShellResult",
    "WorkspaceShellRunner",
]
