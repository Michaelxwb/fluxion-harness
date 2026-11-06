"""[S-11][E-05] 在真实 bwrap 中执行 workspace_shell 探针。

Linux 原生有 bwrap 时直接跑；macOS 无 bwrap 时把同一探针放进带 bwrap 的 Linux 测试
镜像（`system_deps_support.probe_image()`），不 mock runner、bwrap、PATH 或文件系统。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.acceptance.user_skill_sandbox import system_deps_support as system_support

ROOT = Path(__file__).resolve().parents[3]
PROBE = Path(__file__).with_name("workspace_probe.py")
PROBE_TIMEOUT_SEC = 300


def _parse(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload: dict[str, object] = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = {"ok": False, "error": "probe output is not JSON"}
    assert completed.returncode == 0, (
        "workspace_shell bwrap probe failed\n"
        f"exit={completed.returncode}\nstdout={completed.stdout[-4000:]}\n"
        f"stderr={completed.stderr[-4000:]}"
    )
    assert payload.get("ok") is True, (
        f"workspace_shell bwrap probe reported failure: {payload}\n"
        f"stdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-4000:]}"
    )
    return payload


def _native(mode: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(ROOT / "packages/agent-core/src"), env.get("PYTHONPATH", ""))
        if value
    )
    env["MUAD_WORKSPACE_PROBE_MODE"] = mode
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SEC,
        env=env,
    )
    return _parse(completed)


def _image_platform(docker: str, image: str) -> str:
    completed = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Architecture}}", image],
        capture_output=True,
        text=True,
        check=True,
    )
    architecture = completed.stdout.strip() or "amd64"
    return f"linux/{architecture}"


def _docker(mode: str) -> dict[str, object]:
    docker = shutil.which("docker")
    assert docker is not None, "E-05/S-11 需要 bwrap 或 Docker 才能验证真实沙箱边界"
    image = system_support.probe_image()
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--platform",
            _image_platform(docker, image),
            "--user",
            "10001:10001",
            "--security-opt",
            "seccomp=unconfined",
            "-e",
            "PYTHONPATH=/repo/packages/agent-core/src",
            "-e",
            f"MUAD_WORKSPACE_PROBE_MODE={mode}",
            "-v",
            f"{ROOT}:/repo:ro",
            image,
            "python",
            "/repo/tests/acceptance/user_skill_sandbox/workspace_probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SEC,
    )
    return _parse(completed)


def run_probe(mode: str) -> dict[str, object]:
    if shutil.which("bwrap"):
        return _native(mode)
    return _docker(mode)


__all__ = ["PROBE", "PROBE_TIMEOUT_SEC", "run_probe"]
