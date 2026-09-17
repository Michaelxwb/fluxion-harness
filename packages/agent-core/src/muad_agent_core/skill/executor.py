from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from muad_contracts import ResolvedSkill
from muad_skill_sdk import SkillContext

MAX_OUTPUT_BYTES = 64 * 1024
TERMINATE_GRACE_SEC = 2.0
SCRIPTS_DIR = "scripts"
MAIN_SCRIPT = "main.py"
ENV_ALLOWLIST = ("PATH", "HOME", "LANG")


class SkillArtifactResolver(Protocol):
    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path: ...


class SkillExecutor(Protocol):
    async def execute(
        self,
        *,
        artifact: ResolvedSkill,
        local_path: Path,
        input_data: Mapping[str, Any],
        context: SkillContext,
    ) -> Mapping[str, Any]: ...


class SkillExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class SkillExecutionError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SkillExecutionRequest:
    ready_dir: Path
    input: Mapping[str, Any]
    timeout_sec: float
    env: Mapping[str, str] = field(default_factory=dict)
    cancel_event: asyncio.Event | None = None
    script: Path | None = None


@dataclass(frozen=True, slots=True)
class SkillExecutionResult:
    status: SkillExecutionStatus
    stdout: str = ""
    stderr: str = ""
    result: Mapping[str, Any] | None = None
    duration_ms: int = 0
    exit_code: int | None = None


class ScriptSkillExecutor:
    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        ready_dir = Path(request.ready_dir).resolve()
        script = select_script(ready_dir, request.script)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(script),
            cwd=str(ready_dir),
            env=build_child_env(request.env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
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
    communicate: asyncio.Task[tuple[bytes, bytes]] = asyncio.ensure_future(
        process.communicate(stdin_payload)
    )
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
        stdout_bytes, stderr_bytes = await communicate
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


async def _terminate(process: asyncio.subprocess.Process, communicate: asyncio.Future[Any]) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(communicate), TERMINATE_GRACE_SEC)
    except TimeoutError:
        process.kill()


def select_script(ready_dir: Path, script: Path | None) -> Path:
    if script is None:
        return select_entry_script(ready_dir)
    root = ready_dir.resolve()
    resolved = script.resolve()
    if root not in resolved.parents:
        raise SkillExecutionError("script path escapes the skill package")
    if not resolved.is_file():
        raise SkillExecutionError(f"skill script not found: {script.name}")
    return resolved


def select_entry_script(ready_dir: Path) -> Path:
    scripts_dir = ready_dir / SCRIPTS_DIR
    if not scripts_dir.is_dir():
        raise SkillExecutionError("skill package has no scripts directory")
    main = scripts_dir / MAIN_SCRIPT
    if main.is_file():
        return main.resolve()
    candidates = sorted(path for path in scripts_dir.glob("*.py") if path.is_file())
    if not candidates:
        raise SkillExecutionError("skill package has no scripts")
    return candidates[0].resolve()


def build_child_env(extra: Mapping[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    env.update(extra)
    return env


def decode_capped(data: bytes) -> str:
    return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")


def parse_result(stdout: str) -> Mapping[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
    return None
