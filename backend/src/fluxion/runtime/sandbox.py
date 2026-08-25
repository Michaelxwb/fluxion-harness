from __future__ import annotations

import asyncio
import os
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
    # 显式清空环境：沙箱子进程此前继承宿主全部 env（含
    # FLUXION_SECRET_MASTER_KEY、DB 连接串），模型一句 run_command ["env"]
    # 即可把主密钥取进工具结果。仅保留 PATH 让命令可被定位。
    sanitized_env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin"}
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sanitized_env,
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
            # NOTE: file-read 未做 subpath 收口。尝试限制为最小系统根时，macOS
            # dyld 共享缓存依赖使严格 allow-list 致进程启动即 SIGABRT（exit -6）。
            # env 清空（_run_process）已封堵主密钥经环境变量泄漏的最严重路径；
            # 文件级隔离（dev SQLite / K8s SA token）需跨 seatbelt+bubblewrap 的
            # 分平台 allow-list 调优（含 dyld 缓存路径），单独跟踪，不交付半成品。
            "(allow file-read*)",
        ]
        if request.root_read_only:
            lines.append('(allow file-write* (subpath "/tmp/"))')
        else:
            lines.append("(allow file-write*)")
        # 此前 network_enabled=True 时不追加 (allow network*)，而 (deny default)
        # 已拒绝网络 → 该开关在 macOS 后端永远是 no-op（请求网络的工具静默失败）。
        if request.network_enabled:
            lines.append("(allow network*)")
        else:
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
