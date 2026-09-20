#!/usr/bin/env python3
"""Shared execution substrate: deadline-aware argv subprocess runner.

All check executors (acceptance scenarios, Spec verifiers, session
validators) funnel through here so one entry deadline constrains the whole
chain, commands never pass through a shell, and unrun work is reported as
`incomplete` instead of silently skipped or falsely passed.
"""

from __future__ import annotations

import shlex
import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Mapping, Optional, Sequence

_EXECUTIONS: ContextVar[Optional[dict[str, dict[str, object]]]] = ContextVar("cf_executions", default=None)


@contextmanager
def execution_session() -> Iterator[None]:
    """Share results only inside one validation operation, never across turns."""
    if _EXECUTIONS.get() is not None:
        yield
        return
    token = _EXECUTIONS.set({})
    try:
        yield
    finally:
        _EXECUTIONS.reset(token)


def invalidate_executions() -> None:
    cache = _EXECUTIONS.get()
    if cache is not None:
        cache.clear()


def execution_key(argv: Sequence[str], cwd: str, timeout: float) -> str:
    """Identity of a command under the current process environment."""
    return json.dumps([list(argv), str(Path(cwd).resolve()), float(timeout)], ensure_ascii=False)


def remaining_seconds(deadline: Optional[float]) -> Optional[float]:
    """Seconds left until the absolute monotonic deadline (None = unbounded)."""
    if deadline is None:
        return None
    return deadline - time.monotonic()


def build_argv(template: str, files: Sequence[str]) -> list[str]:
    """Expand a validator command template into argv without a shell.

    `{files}` must be a standalone token and expands to one argv entry per
    file (spaces/metachars safe). Embedded usage is a config error — refusing
    is safer than reintroducing shell splitting.
    """
    tokens = shlex.split(template or "")
    if not tokens:
        return []
    argv: list[str] = []
    for token in tokens:
        if token == "{files}":
            argv.extend(files)
        elif "{files}" in token:
            raise ValueError(f"validator command must keep {{files}} as a standalone token: {template!r}")
        else:
            argv.append(token)
    return argv


def run_command(
    argv: Sequence[str],
    cwd: str,
    timeout: float,
    deadline: Optional[float] = None,
) -> dict[str, object]:
    """Run argv synchronously under timeout+deadline. Never uses a shell."""
    key = execution_key(argv, cwd, timeout)
    cache = _EXECUTIONS.get()
    if cache is not None and key in cache:
        return dict(cache[key])
    result = _execute(argv, cwd, timeout, deadline)
    if cache is not None and result["status"] == "ok":
        cache[key] = result
    return result


def _execute(argv: Sequence[str], cwd: str, timeout: float, deadline: Optional[float]) -> dict[str, object]:
    remaining = remaining_seconds(deadline)
    if remaining is not None and remaining <= 0:
        return {"status": "deadline_exceeded", "returncode": None, "stdout": "", "stderr": ""}
    effective = min(timeout, remaining) if remaining is not None else timeout
    try:
        # start_new_session：子进程成为独立进程组组长，超时时可整组回收。
        # 否则只杀直接子进程，其派生的 node/uvicorn/vite 会成为孤儿并继续占用端口。
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {"status": "spawn_error", "returncode": None, "stdout": "", "stderr": str(exc)}
    try:
        stdout, stderr = proc.communicate(timeout=max(effective, 0.01))
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        stdout, stderr = proc.communicate()
        return {"status": "timeout", "returncode": None, "stdout": (stdout or "")[-4000:], "stderr": ""}
    return {"status": "ok", "returncode": proc.returncode, "stdout": stdout or "", "stderr": stderr or ""}


def _kill_process_group(proc: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
