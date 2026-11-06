"""真实 bwrap 验收探针的镜像/容器支持。

本机（macOS）没有 bwrap：测试把探针放进带 bwrap 的 Linux 镜像执行。镜像只预装
agent-core/skill-sdk 的第三方依赖（pydantic/redis/PyYAML/httpx），仓库源码通过
``/repo`` 只读挂载；探针内仍然走真实 ``BwrapRunner``/``bwrap``/PID namespace。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = Path(__file__).resolve().parent / "fixtures" / "skill_executor" / "Dockerfile"
PROBE = Path(__file__).with_name("sandboxed_probe.py")
DEFAULT_BASE_IMAGES = (
    "fx-sandbox-test:local",
    "docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106",
)
DEPENDENCIES = (
    "fastapi>=0.115,<1",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "redis>=5,<7",
    "PyYAML>=6,<7",
    "httpx>=0.28,<1",
    "sqlalchemy[asyncio]>=2.0,<3",
)
RUN_TIMEOUT_SEC = 300
BUILD_TIMEOUT_SEC = 900


def _docker() -> str:
    binary = shutil.which("docker")
    if binary is None:
        raise RuntimeError("TASK-003 验收需要 Docker 才能验证真实 bwrap 边界")
    return binary


def _run(argv: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False
    )


def _image_exists(image: str) -> bool:
    binary = shutil.which("docker")
    return bool(binary) and _run([binary, "image", "inspect", image], timeout=30).returncode == 0


def _base_image() -> str:
    configured = os.environ.get("MUAD_SKILL_EXECUTOR_BASE_IMAGE")
    candidates = (configured,) if configured else DEFAULT_BASE_IMAGES
    for image in candidates:
        if image and _image_exists(image):
            return image
    raise RuntimeError(
        "找不到带 bwrap 的测试基础镜像；请安装 bubblewrap 或设置 "
        f"MUAD_SKILL_EXECUTOR_BASE_IMAGE（候选：{DEFAULT_BASE_IMAGES}）"
    )


def probe_image() -> str:
    """按依赖清单内容寻址缓存测试镜像；不 mock bwrap/文件系统。"""
    configured = os.environ.get("MUAD_SKILL_EXECUTOR_TEST_IMAGE")
    if configured:
        if not _image_exists(configured):
            raise RuntimeError(f"MUAD_SKILL_EXECUTOR_TEST_IMAGE 不存在: {configured}")
        return configured
    digest = hashlib.sha256("\n".join(DEPENDENCIES).encode("utf-8")).hexdigest()[:12]
    tag = f"muad-skill-executor-probe:{digest}"
    if _image_exists(tag):
        return tag
    base = _base_image()
    command = [
        _docker(),
        "build",
        "--platform",
        "linux/amd64",
        "--build-arg",
        f"BASE_IMAGE={base}",
        "--build-arg",
        f"PIP_INDEX_URL={os.environ.get('MUAD_SKILL_EXECUTOR_PIP_INDEX_URL', 'https://pypi.org/simple')}",
        "-t",
        tag,
        "-f",
        str(DOCKERFILE),
        ".",
    ]
    result = _run(command, timeout=BUILD_TIMEOUT_SEC)
    if result.returncode != 0:
        raise RuntimeError(
            "构建 Skill 执行器探针镜像失败\n"
            f"command={command}\nstdout={result.stdout[-4000:]}\nstderr={result.stderr[-4000:]}"
        )
    return tag


def run_probe(
    scenario: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout: int = RUN_TIMEOUT_SEC,
) -> dict[str, object]:
    image = probe_image()
    command = [
        _docker(),
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--security-opt",
        "seccomp=unconfined",
        "-v",
        f"{ROOT}:/repo:ro",
    ]
    for key, value in (env or {}).items():
        command.extend(("-e", f"{key}={value}"))
    command.extend(
        (
            image,
            "python",
            "/repo/tests/acceptance/user_skill_sandbox/sandboxed_probe.py",
            "--scenario",
            scenario,
        )
    )
    result = _run(command, timeout=timeout)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0:
        raise AssertionError(
            f"Skill 执行器探针失败 scenario={scenario} exit={result.returncode}\n"
            f"stdout={result.stdout[-6000:]}\nstderr={result.stderr[-6000:]}"
        )
    if not lines:
        raise AssertionError(f"探针没有输出 JSON: scenario={scenario}\nstderr={result.stderr[-4000:]}")
    import json

    payload = json.loads(lines[-1])
    assert isinstance(payload, dict), payload
    return payload
