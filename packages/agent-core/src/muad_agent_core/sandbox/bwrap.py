"""Bubblewrap 探针与 argv 构造。"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from .runner import _execute, _require_directory
from .spec import (
    SANDBOX_DEFAULT_PATH,
    SandboxMount,
    SandboxResult,
    SandboxSpec,
    SandboxUnavailable,
    sanitize_env,
)

logger = logging.getLogger("muad_agent_core.sandbox")

MIN_BWRAP_VERSION: tuple[int, int, int] = (0, 13, 1)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_PROBE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True, slots=True)
class BwrapProbe:
    path: str
    version: tuple[int, int, int]
    raw_version: str


def probe_bwrap(
    bwrap_path: str | None = None,
    *,
    min_version: tuple[int, int, int] = MIN_BWRAP_VERSION,
) -> BwrapProbe:
    """真实执行 ``bwrap --version``；缺失/超时/版本不兼容一律 SANDBOX_UNAVAILABLE。"""
    path = bwrap_path or shutil.which("bwrap")
    if not path:
        raise SandboxUnavailable("bwrap executable not found; refusing to fall back to host execution")
    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailable(f"bwrap probe failed: {exc}") from exc
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        raise SandboxUnavailable(
            f"bwrap probe failed with exit {completed.returncode}: {output[:200]}"
        )
    match = _VERSION_RE.search(output)
    if match is None:
        raise SandboxUnavailable(f"bwrap version is not recognized: {output[:200]!r}")
    version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if version < min_version:
        minimum = ".".join(str(part) for part in min_version)
        raise SandboxUnavailable(
            f"bwrap {output!r} is older than required {minimum}; refusing to fall back"
        )
    return BwrapProbe(path=str(path), version=version, raw_version=output)


def build_bwrap_argv(spec: SandboxSpec, bwrap_path: str) -> list[str]:
    """把受校验的 SandboxSpec 编译成 bwrap argv（服务端生成，模型不可传）。"""
    policy = spec.policy
    limits = spec.effective_limits
    env = sanitize_env(spec.env, default_path=SANDBOX_DEFAULT_PATH, home="/tmp")

    argv = [bwrap_path, "--unshare-all", "--die-with-parent", "--new-session"]
    if policy.network:
        argv.append("--share-net")
    argv.append("--clearenv")
    for key, value in env.items():
        argv.extend(("--setenv", key, value))

    for mount in policy.system_mounts:
        argv.extend(_mount_args(mount))
    argv.extend(("--dev", "/dev"))
    argv.extend(("--size", str(limits.tmpfs_bytes), "--tmpfs", "/tmp"))

    if spec.package_dir is not None:
        argv.extend(("--ro-bind", str(spec.package_dir), "/skill"))
    argv.extend(("--bind", str(spec.workspace_dir), "/workspace"))
    for mount in spec.mounts:
        argv.extend(_mount_args(mount))

    argv.extend(("--chdir", spec.cwd))
    argv.append("--")
    argv.extend(spec.command)
    return argv


def _mount_args(mount: SandboxMount) -> list[str]:
    if mount.readonly:
        flag = "--ro-bind-try" if mount.optional else "--ro-bind"
    else:
        flag = "--bind-try" if mount.optional else "--bind"
    return [flag, str(mount.source), mount.target]


class BwrapRunner:
    name = "bwrap"

    def __init__(self, probe: BwrapProbe, *, observer: object | None = None) -> None:
        if probe.version < MIN_BWRAP_VERSION:
            raise SandboxUnavailable(
                f"bwrap {probe.raw_version!r} is not supported"
            )
        self._probe = probe
        self._observer = observer

    @property
    def version(self) -> tuple[int, int, int]:
        return self._probe.version

    @property
    def bwrap_path(self) -> str:
        return self._probe.path

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        _require_directory(spec.workspace_dir, "workspace")
        if spec.package_dir is not None:
            _require_directory(spec.package_dir, "package")
        argv = build_bwrap_argv(spec, self._probe.path)
        # bwrap itself only needs a minimal env; --clearenv handles the sandboxed child.
        env = {"PATH": SANDBOX_DEFAULT_PATH, "HOME": "/tmp"}
        return await _execute(spec, argv, cwd=None, env=env)


__all__ = [
    "BwrapProbe",
    "BwrapRunner",
    "MIN_BWRAP_VERSION",
    "build_bwrap_argv",
    "probe_bwrap",
]
