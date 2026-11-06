"""SandboxRunner 协议、dev-unsafe 实现与进程树回收引擎。"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import resource
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .spec import (
    TERMINATE_GRACE_SEC,
    SandboxCancelled,
    SandboxError,
    SandboxLimits,
    SandboxOutputLimit,
    SandboxResult,
    SandboxSpec,
    SandboxTimeout,
    SandboxUnavailable,
    default_host_path,
    sanitize_env,
)

logger = logging.getLogger("muad_agent_core.sandbox")

_OUTPUT_CHUNK_BYTES = 64 * 1024
_PR_SET_PDEATHSIG = 1


@runtime_checkable
class SandboxRunner(Protocol):
    name: str

    async def run(self, spec: SandboxSpec) -> SandboxResult: ...


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    kind: str
    detail: Mapping[str, str] = field(default_factory=dict)


class DevUnsafeRunner:
    """仅本地开发显式开启：直接执行宿主进程，但仍做进程组回收、rlimit 与 env 过滤。"""

    name = "dev-unsafe"

    def __init__(self, *, observer: object | None = None) -> None:
        self._observer = observer

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        if not spec.policy.dev_unsafe:
            raise SandboxUnavailable(
                "dev-unsafe runner requires SandboxPolicy(dev_unsafe=True); "
                "the default policy never falls back to host execution"
            )
        _emit(
            self._observer,
            "sandbox_dev_unsafe_execution",
            {
                "name": spec.name,
                "profile": str(spec.policy.profile),
                "workspace": str(spec.workspace_dir),
                "executable": os.path.basename(spec.command[0]),
                "args": str(len(spec.command) - 1),
            },
        )
        _require_directory(spec.workspace_dir, "workspace")
        if spec.package_dir is not None:
            _require_directory(spec.package_dir, "package")
        env = sanitize_env(
            spec.env,
            default_path=default_host_path(),
            home="/tmp",
            extra_keys=spec.policy.env_inject,
        )
        return await _execute(
            spec,
            spec.command,
            cwd=spec.workspace_dir,
            env=env,
        )


class DisabledRunner:
    """显式 ``sandbox_mode=disabled``：只允许 dev；每次执行都 fail closed。"""

    name = "disabled"

    def __init__(self, *, observer: object | None = None) -> None:
        self._observer = observer

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        _emit(self._observer, "sandbox_unavailable", {"mode": "disabled", "reason": "disabled"})
        raise SandboxUnavailable("sandbox is disabled; refusing to execute user code")


def _emit(observer: object | None, kind: str, detail: dict[str, str]) -> None:
    logger.info("sandbox_event kind=%s detail=%s", kind, detail)
    if observer is not None:
        observer(SandboxEvent(kind=kind, detail=dict(detail)))  # type: ignore[operator]


def _require_directory(path: object, label: str) -> None:
    from pathlib import Path

    resolved = Path(str(path))
    if not resolved.is_dir():
        raise SandboxUnavailable(f"sandbox {label} directory is unavailable: {resolved}")


@dataclass(slots=True)
class _Collector:
    limit: int
    data: bytearray = field(default_factory=bytearray)
    total: int = 0
    overflow: bool = False

    async def pump(self, stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(_OUTPUT_CHUNK_BYTES)
            if not chunk:
                return
            self.total += len(chunk)
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if self.total > self.limit:
                self.overflow = True

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


async def _write_stdin(process: asyncio.subprocess.Process, payload: bytes) -> None:
    assert process.stdin is not None
    try:
        if payload:
            process.stdin.write(payload)
            await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with contextlib.suppress(Exception):
            process.stdin.close()


def _set_limit(kind: int, value: int | None) -> None:
    if value is None:
        return
    try:
        _, hard = resource.getrlimit(kind)
    except (OSError, ValueError):
        return
    ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
    try:
        resource.setrlimit(kind, (ceiling, ceiling))
    except (OSError, ValueError):
        return


def _apply_limits(limits: SandboxLimits) -> None:
    for name, value in (
        ("RLIMIT_AS", limits.address_space_bytes),
        ("RLIMIT_CPU", limits.cpu_seconds),
        ("RLIMIT_NPROC", limits.max_processes),
        ("RLIMIT_FSIZE", limits.max_file_bytes),
        ("RLIMIT_NOFILE", limits.open_files),
    ):
        kind = getattr(resource, name, None)
        if kind is not None:
            _set_limit(kind, value)


def _set_parent_death_signal() -> None:
    """Linux 上让子进程在 runner 意外退出时收到 SIGKILL（macOS 无 prctl）。"""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except (OSError, AttributeError):
        return


def _preexec(limits: SandboxLimits) -> Callable[[], None]:
    def apply() -> None:
        _apply_limits(limits)
        _set_parent_death_signal()

    return apply


def _signal_group(process: asyncio.subprocess.Process, sig: int) -> None:
    """信号发给子进程所在**整个进程组**；组 id 直接用 process.pid（start_new_session）。"""
    try:
        os.killpg(process.pid, sig)
    except OSError:
        with contextlib.suppress(OSError):
            process.send_signal(sig)


async def _terminate(process: asyncio.subprocess.Process, wait_task: asyncio.Task[int]) -> None:
    """SIGTERM → 有界等待 → SIGKILL 整组；不按 returncode 早返回（后代可能还活着）。"""
    _signal_group(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), TERMINATE_GRACE_SEC)
    except TimeoutError:
        pass
    # 子进程自己退出但 setsid/双重 fork 的后代仍在组里时，SIGTERM 可能被忽略；
    # 升级 SIGKILL 必须在组仍有成员时发出（组空了 killpg 会 ESRCH，无副作用）。
    _signal_group(process, signal.SIGKILL)
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), TERMINATE_GRACE_SEC)
    except TimeoutError:
        logger.warning("sandbox_process_wait_timeout pid=%s", process.pid)


async def _drain(task: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), TERMINATE_GRACE_SEC)
    except TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _cancel_task(task: asyncio.Task[object] | None) -> None:
    if task is not None and not task.done():
        task.cancel()


async def _execute(
    spec: SandboxSpec,
    command: Iterable[str],
    *,
    cwd: object | None,
    env: dict[str, str],
) -> SandboxResult:
    started = time.monotonic()
    argv = tuple(command)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_preexec(spec.effective_limits),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailable(f"failed to start sandbox process: {exc}") from exc

    assert process.stdout is not None and process.stderr is not None
    stdout = _Collector(spec.output_limit_bytes)
    stderr = _Collector(spec.output_limit_bytes)
    stdout_task = asyncio.create_task(stdout.pump(process.stdout))
    stderr_task = asyncio.create_task(stderr.pump(process.stderr))
    wait_task = asyncio.create_task(process.wait())
    stdin_task = asyncio.create_task(_write_stdin(process, spec.stdin))
    cancel_task = asyncio.create_task(spec.cancel_event.wait()) if spec.cancel_event is not None else None

    timed_out = cancelled = overflow = False
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + spec.timeout_sec
        watched: set[asyncio.Task[object]] = {wait_task, stdout_task, stderr_task}
        if cancel_task is not None:
            watched.add(cancel_task)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            done, _ = await asyncio.wait(watched, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                timed_out = True
                break
            if cancel_task is not None and cancel_task in done and cancel_task.result():
                cancelled = True
                break
            if stdout.overflow or stderr.overflow:
                overflow = True
                break
            if wait_task in done:
                break
            watched.difference_update(done)

        if timed_out or cancelled or overflow:
            await _terminate(process, wait_task)
        await _drain(stdout_task)
        await _drain(stderr_task)

        result = SandboxResult(
            exit_code=process.returncode,
            stdout=stdout.text(),
            stderr=stderr.text(),
            duration_ms=int((time.monotonic() - started) * 1000),
            output_truncated=overflow,
        )
        if overflow:
            raise SandboxOutputLimit(
                f"sandbox output exceeded {spec.output_limit_bytes} bytes", result=result
            )
        if cancelled:
            raise SandboxCancelled("sandbox execution was cancelled", result=result)
        if timed_out:
            raise SandboxTimeout(f"sandbox execution exceeded {spec.timeout_sec:g}s", result=result)
        return result
    except asyncio.CancelledError:
        await _terminate(process, wait_task)
        await _drain(stdout_task)
        await _drain(stderr_task)
        raise
    finally:
        _cancel_task(cancel_task)
        _cancel_task(stdin_task)
        _cancel_task(wait_task if not wait_task.done() else None)
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()


__all__ = [
    "DevUnsafeRunner",
    "DisabledRunner",
    "SandboxError",
    "SandboxResult",
    "SandboxRunner",
]
