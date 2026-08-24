from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SandboxError(RuntimeError):
    code = "sandbox_error"


class SandboxUnavailableError(SandboxError):
    code = "sandbox_unavailable"


class SandboxTimeoutError(SandboxError):
    code = "sandbox_timeout"


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    command: list[str]
    cwd: str | None = None
    timeout_ms: int = 1000
    network_enabled: bool = False
    root_read_only: bool = True


@dataclass(frozen=True, slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int


class SandboxBackend(Protocol):
    async def run(self, request: SandboxRequest) -> SandboxResult: ...


class RecordingSandboxBackend:
    def __init__(self, *, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self._result = SandboxResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
        self.requests: list[SandboxRequest] = []

    async def run(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return self._result


async def _run_process(
    argv: list[str],
    *,
    cwd: str | None,
    timeout_ms: int,
) -> SandboxResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_ms / 1000,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise SandboxTimeoutError("sandbox command timed out") from exc
    return SandboxResult(
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        exit_code=process.returncode or 0,
    )


class SandboxExecBackend:
    """macOS 原生后端：sandbox-exec（Seatbelt profile）隔离执行。

    sandbox-exec 在较新 macOS 已标记 deprecated（Apple 建议迁移 App Sandbox），
    但当前可用且能真实隔离文件系统与网络；V1 作为 macOS 原生后端。
    """

    def __init__(self, sandbox_exec_path: str = "/usr/bin/sandbox-exec") -> None:
        self.sandbox_exec_path = sandbox_exec_path

    def _profile(self, request: SandboxRequest) -> str:
        lines = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow sysctl-read)",
            "(allow file-read*)",
        ]
        if request.root_read_only:
            lines.append('(allow file-write* (subpath "/tmp/"))')
        else:
            lines.append("(allow file-write*)")
        if not request.network_enabled:
            lines.append("(deny network*)")
        return "\n".join(lines) + "\n"

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not request.command:
            raise SandboxError("empty command")
        argv = [self.sandbox_exec_path, "-p", self._profile(request), *request.command]
        return await _run_process(argv, cwd=request.cwd, timeout_ms=request.timeout_ms)


class BubblewrapSandboxBackend:
    """Linux 生产后端：bubblewrap（namespace/seccomp）隔离执行。

    依赖 Linux 内核能力，macOS 上无法运行；本机以 argv 构造测试验证接线，
    真实隔离效果在 Linux 环境执行 bwrap 验证（FEAT-21）。
    """

    def __init__(self, bwrap_path: str) -> None:
        self.bwrap_path = bwrap_path

    def build_argv(self, request: SandboxRequest) -> list[str]:
        argv = [self.bwrap_path]
        # 隔离 namespace（user/pid/net/ipc/uts/cgroup/time）、父进程退出则沙箱终止
        argv += ["--unshare-all", "--die-with-parent"]
        # 只读根文件系统，/tmp 为可写临时区，挂载 procfs
        argv += ["--ro-bind", "/", "/", "--tmpfs", "/tmp", "--proc", "/proc"]
        if request.network_enabled:
            argv += ["--share-net"]
        argv += ["--", *request.command]
        return argv

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not request.command:
            raise SandboxError("empty command")
        return await _run_process(
            self.build_argv(request),
            cwd=request.cwd,
            timeout_ms=request.timeout_ms,
        )


class DevSandboxBackend:
    """dev 降级后端：直接执行命令，无进程隔离（显式标注非生产，FEAT-21）。"""

    async def run(self, request: SandboxRequest) -> SandboxResult:
        if not request.command:
            raise SandboxError("empty command")
        return await _run_process(
            list(request.command),
            cwd=request.cwd,
            timeout_ms=request.timeout_ms,
        )


class SandboxBackendRegistry:
    def resolve(
        self,
        *,
        platform_system: str,
        bwrap_path: str | None,
        allow_dev_fallback: bool = False,
    ) -> SandboxBackend:
        normalized = platform_system.lower()
        if normalized == "linux" and bwrap_path:
            return BubblewrapSandboxBackend(bwrap_path)
        if normalized in ("darwin", "macos"):
            return SandboxExecBackend()
        if allow_dev_fallback:
            return DevSandboxBackend()
        raise SandboxUnavailableError(f"no native sandbox backend for {platform_system}")


def ensure_allowed_cwd(cwd: str | None, allowed_roots: tuple[Path, ...]) -> str | None:
    if cwd is None:
        return None
    resolved = Path(cwd).resolve()
    for root in allowed_roots:
        resolved_root = root.resolve()
        if resolved == resolved_root or resolved_root in resolved.parents:
            return str(resolved)
    raise SandboxUnavailableError(f"cwd {cwd} is outside allowed roots")
