"""[E-02] SandboxRunner → bwrap → 完整进程树（真实边界，不 mock）。

本机（macOS）没有 bwrap 时，测试会把同一个探针脚本放进带 bwrap 的 Linux 测试镜像里执行，
而不是 mock runner 或跳过：探针内部仍然走真实的 ``BwrapRunner``、真实的 ``bwrap``、
真实的进程组/信号/PID namespace。镜像可用 ``MUAD_SANDBOX_TEST_IMAGE`` 覆盖。

真实边界：SandboxRunner → bwrap → 超时/取消/父进程退出 → 完整后代回收。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE = Path(__file__).with_name("e_02_probe.py")
DEFAULT_IMAGES = (
    "fx-sandbox-test:local",
    "docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106",
    "docker.sangfor.com/mss-base/sandbox-session-probe:0.1",
)
PROBE_TIMEOUT_SEC = 240


def _pythonpath() -> str:
    return str(ROOT / "packages" / "agent-core" / "src")


def _native_bwrap() -> str | None:
    return shutil.which("bwrap")


def _docker_binary() -> str | None:
    return shutil.which("docker")


def _docker_image() -> str | None:
    configured = os.environ.get("MUAD_SANDBOX_TEST_IMAGE")
    candidates = (configured,) if configured else DEFAULT_IMAGES
    docker = _docker_binary()
    if docker is None:
        return None
    for image in candidates:
        assert image is not None
        completed = subprocess.run(
            [docker, "image", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return image
    return None


def _image_platform(docker: str, image: str) -> str:
    completed = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Architecture}}", image],
        capture_output=True,
        text=True,
        check=True,
    )
    architecture = completed.stdout.strip() or "amd64"
    return f"linux/{architecture}"


def _parse_probe(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload: dict[str, object] = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = {"ok": False, "error": "probe output is not JSON"}
    assert completed.returncode == 0, (
        "E-02 bwrap probe failed\n"
        f"exit={completed.returncode}\nstdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-4000:]}"
    )
    assert payload.get("ok") is True, (
        "E-02 bwrap probe reported failure\n"
        f"payload={payload}\nstdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-4000:]}"
    )
    return payload


def _run_native(python: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (_pythonpath(), env.get("PYTHONPATH", "")) if value
    )
    completed = subprocess.run(
        [python, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SEC,
        env=env,
    )
    return _parse_probe(completed)


def _run_in_docker(image: str) -> dict[str, object]:
    docker = _docker_binary()
    assert docker is not None
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--platform",
            _image_platform(docker, image),
            "--security-opt",
            "seccomp=unconfined",
            "-e",
            "PYTHONPATH=/repo/packages/agent-core/src",
            "-v",
            f"{ROOT}:/repo:ro",
            image,
            "python",
            "/repo/tests/acceptance/user_skill_sandbox/e_02_probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SEC,
    )
    return _parse_probe(completed)


def test_e02_timeout_cancel_parent_death_reap_complete_tree() -> None:
    native = _native_bwrap()
    if native is not None:
        payload = _run_native(shutil.which("python") or "python3")
    else:
        image = _docker_image()
        assert image is not None, (
            "E-02 需要 bwrap 才能验证真实进程树回收：本机没有 bwrap，也没有可用的 "
            "bwrap 测试镜像。请安装 bubblewrap，或设置 MUAD_SANDBOX_TEST_IMAGE。"
        )
        payload = _run_in_docker(image)

    timeout = payload["timeout"]
    cancel = payload["cancel"]
    parent_death = payload["parent_death"]
    subsequent = payload["subsequent"]

    assert timeout["status"] == "SANDBOX_TIMEOUT"
    assert timeout["orphans"] == [], f"超时后仍有孤儿: {timeout}"
    assert cancel["status"] == "SANDBOX_CANCELLED"
    assert cancel["orphans"] == [], f"取消后仍有孤儿: {cancel}"
    assert parent_death["orphans"] == [], f"父进程退出后仍有孤儿: {parent_death}"
    assert subsequent["exit_code"] == 0
    assert "ok-after-timeout" in subsequent["stdout"]
