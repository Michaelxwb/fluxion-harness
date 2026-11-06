from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from muad_platform_sdk.adapter.mss_config import PlatformAuthError
from muad_platform_sdk.skill_bridge import skill_platform_environment
from muad_skill_sdk import SkillPackage, SkillPackageError, locate_package_root
from redis.exceptions import RedisError

from .contracts import (
    MAX_OUTPUT_BYTES,
    SkillArtifactResolver,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillExecutor,
)
from .selection import script_command, select_entry_script, select_script

TERMINATE_GRACE_SEC = 2.0
ENV_ALLOWLIST = ("PATH", "HOME", "LANG")


class ScriptSkillExecutor:
    """宿主进程执行器：只用于本地开发/测试，不提供沙箱隔离。

    Runtime/Worker 的装配必须使用 ``SandboxedSkillExecutor``；保留本类是为了让
    本地脚本调试与旧单元测试仍能在无 bwrap 的机器上跑通。
    """

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        from dataclasses import replace

        try:
            name = ""
            if request.platform_context and request.platform_context.platform_keys:
                root, _ = locate_package_root(request.ready_dir)
                name = SkillPackage.load(root).manifest.name
            async with skill_platform_environment(request.platform_context, name) as session:
                return await self._execute(replace(request, env={**request.env, **session.env}))
        except SkillPackageError as exc:
            raise SkillExecutionError(exc.message) from exc
        except PlatformAuthError as exc:
            raise SkillExecutionError(exc.code) from None
        except RedisError:
            raise SkillExecutionError("PLATFORM_SESSION_STORE_UNAVAILABLE") from None

    async def _execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        ready_dir = Path(request.ready_dir).resolve()
        if not (ready_dir / "scripts").is_dir() and not (ready_dir / "SKILL.md").is_file():
            try:
                ready_dir, _ = locate_package_root(ready_dir)
            except SkillPackageError as exc:
                raise SkillExecutionError(exc.message) from exc
        script = select_script(ready_dir, request.script)
        started = time.monotonic()
        env = build_child_env(request.env)
        try:
            process = await asyncio.create_subprocess_exec(
                *script_command(script, env),
                cwd=str(ready_dir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # 自成会话/进程组，超时与取消才能**连孙进程一起**回收（见 `_terminate`）
                start_new_session=True,
            )
        except OSError as exc:
            raise SkillExecutionError("failed to start skill interpreter") from exc
        status, stdout_bytes, stderr_bytes = await _collect(process, request)
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = decode_capped(stdout_bytes)
        stderr = decode_capped(stderr_bytes)
        return SkillExecutionResult(
            status=status,
            stdout=stdout,
            stderr=stderr,
            result=parse_result(stdout) if status is SkillExecutionStatus.SUCCEEDED else None,
            duration_ms=duration_ms,
            exit_code=process.returncode,
        )


async def _collect(
    process: asyncio.subprocess.Process,
    request: SkillExecutionRequest,
) -> tuple[SkillExecutionStatus, bytes, bytes]:
    stdin_payload = json.dumps(dict(request.input), ensure_ascii=False).encode("utf-8")
    communicate: asyncio.Task[tuple[bytes, bytes]] = asyncio.ensure_future(process.communicate(stdin_payload))
    cancel_waiter = asyncio.ensure_future(request.cancel_event.wait()) if request.cancel_event else None
    timed_out = False
    cancelled = False
    try:
        watched: set[asyncio.Future[Any]] = {communicate}
        if cancel_waiter is not None:
            watched.add(cancel_waiter)
        try:
            done, _ = await asyncio.wait(
                watched,
                timeout=request.timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await _terminate(process, communicate)
            raise
        timed_out = not done
        cancelled = cancel_waiter is not None and cancel_waiter in done and communicate not in done
        if timed_out or cancelled:
            await _terminate(process, communicate)
        stdout_bytes, stderr_bytes = await _drain_output(communicate)
    finally:
        if cancel_waiter is not None:
            cancel_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_waiter
    if cancelled:
        return SkillExecutionStatus.CANCELLED, stdout_bytes, stderr_bytes
    if timed_out:
        return SkillExecutionStatus.TIMED_OUT, stdout_bytes, stderr_bytes
    if process.returncode != 0:
        return SkillExecutionStatus.FAILED, stdout_bytes, stderr_bytes
    return SkillExecutionStatus.SUCCEEDED, stdout_bytes, stderr_bytes


async def _drain_output(communicate: asyncio.Future[tuple[bytes, bytes]]) -> tuple[bytes, bytes]:
    """取回子进程输出，**有界等待**。

    正常路径上 `communicate` 早就完成了，这个等待是零成本的；只有终止之后仍等不到 EOF
    （管道还被谁抓着）时才会超时。那时宁可丢掉输出，也不能把 worker 无限期钉在这儿
    —— `_terminate` 负责把整组杀掉，这里负责"即使杀不掉也不挂死"（2026-10-03 review）。
    """
    try:
        return await asyncio.wait_for(communicate, TERMINATE_GRACE_SEC)
    except TimeoutError:
        return b"", b""


def _signal_group(process: asyncio.subprocess.Process, sig: int) -> None:
    """把信号发给子进程**所在的整个进程组**，而不只是它自己。

    Skill 脚本自己 spawn 的孙进程不挂在 `process.pid` 上：只 `terminate()/kill()` 直接子进程
    会把它们留成孤儿继续跑（占端口、写文件、抓着 stdout 管道）。Python 与 Node 同理 —— 这
    不是 JS 独有的问题。

    组 id 直接用 `process.pid`：子进程以 `start_new_session=True` 启动，`setsid()` 之后它
    既是会话首进程也是组长，组 id 就等于自己的 pid。**不调 `os.getpgid(pid)`** 是有意的：
    子进程被回收之后 `getpgid` 就查不到了，而"孙进程还抓着管道"恰恰发生在它已退出之后 ——
    那时唯一还有效的线索就是组 id 本身（组里只要还有活着的成员，组 id 就不会被回收）。
    """
    try:
        os.killpg(process.pid, sig)
    except OSError:
        # 组已消失或权限不足：退回只发给直接子进程，不吞掉这次终止
        with contextlib.suppress(OSError):
            process.send_signal(sig)


async def _terminate(process: asyncio.subprocess.Process, communicate: asyncio.Future[Any]) -> None:
    """超时/取消时回收**整棵进程树**。

    这里**不按 `process.returncode` 早返回**：子进程自己先退出、而它 spawn 的孙进程还抓着
    stdout 管道，是真实存在的形状。早返回会同时造成两个后果 —— 孙进程不被回收，且后面的
    `await communicate()` 永远等不到 EOF，**worker 挂死**（2026-10-03 review）。
    """
    _signal_group(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(communicate), TERMINATE_GRACE_SEC)
    except TimeoutError:
        _signal_group(process, signal.SIGKILL)


def build_child_env(extra: Mapping[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    env.update(extra)
    return env


def decode_capped(data: bytes) -> str:
    return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")


def parse_result(stdout: str) -> Mapping[str, object] | None:
    """从**末尾往前**找第一个可解析为 JSON 对象的非空行。

    结果行**不必是最后一行**：脚本常会在结果之后再打印一行日志。旧实现只看最后一行、
    解析失败就 `return None`，而 `interpret_execution` 在 `result is None` 时会把**整坨
    stdout**（含那行日志）当成 `{"text": ...}` 交给模型——结构化结果被日志吞掉，且失败
    语义从"空结果"悄悄变成"一坨日志文本"（2026-10-03 review）。

    跳过非 JSON 行**不会**放宽成功路径：旧行为能解析出的最后一行，新行为第一个就命中它。
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


__all__ = [
    "MAX_OUTPUT_BYTES",
    "ScriptSkillExecutor",
    "SkillArtifactResolver",
    "SkillExecutionError",
    "SkillExecutionRequest",
    "SkillExecutionResult",
    "SkillExecutionStatus",
    "SkillExecutor",
    "build_child_env",
    "decode_capped",
    "parse_result",
    "script_command",
    "select_entry_script",
    "select_script",
]
