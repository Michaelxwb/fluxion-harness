"""[S-48][E-17] 系统依赖基线的真实镜像/沙箱测试夹具。

测试只使用真实 Docker 镜像、真实 bwrap 和真实文件系统：
- 探针镜像从内置 bwrap 的基础镜像构建，执行与 runtime/worker 相同的
  `build/docker-build/skill-deps/install.sh`；
- 容器内以 UID 10001 运行，`--unshare-all` 的 bwrap 中完成 import/require；
- 不 mock Docker、bwrap、pip 或 site-packages。

镜像按基线内容寻址缓存；设置 `MUAD_SYSTEM_DEPS_TEST_IMAGE` 可直接复用
已经构建好的 runtime/worker 镜像（内置 bwrap 且已安装基线依赖）。
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SKILL_DEPS_DIR = ROOT / "build/docker-build/skill-deps"
PROBE_DOCKERFILE = Path(__file__).resolve().parent / "fixtures" / "system_deps" / "Dockerfile"
SANDBOX_PATH = "/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin"
DEFAULT_PROBE_BASES = (
    "docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106",
    "fx-sandbox-test:local",
    "docker.sangfor.com/mss-base/python-node:3.12.13-node24",
)
BUILD_TIMEOUT_SEC = 900
RUN_TIMEOUT_SEC = 300


def load_system_deps_module() -> ModuleType:
    """加载被测脚本；脚本缺失时直接暴露为测试失败，而不是跳过。"""
    path = ROOT / "scripts/skill_system_deps.py"
    spec = importlib.util.spec_from_file_location("skill_system_deps_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _docker() -> str:
    binary = shutil.which("docker")
    if binary is None:
        raise RuntimeError("S-48/E-17 需要 Docker 才能验证真实镜像与 bwrap 边界")
    return binary


def _run(argv: Sequence[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def image_exists(image: str) -> bool:
    binary = shutil.which("docker")
    if binary is None:
        return False
    return _run([binary, "image", "inspect", image], timeout=30).returncode == 0


def _probe_base_image() -> str:
    configured = os.environ.get("MUAD_SYSTEM_DEPS_TEST_BASE")
    candidates = (configured,) if configured else DEFAULT_PROBE_BASES
    for image in candidates:
        if image and image_exists(image):
            return image
    raise RuntimeError(
        "找不到带 Node 24 + bwrap 的测试基础镜像；请设置 MUAD_SYSTEM_DEPS_TEST_BASE "
        f"或先构建 {DEFAULT_PROBE_BASES[0]}"
    )


def probe_image() -> str:
    """返回已安装系统依赖基线、带 bwrap 的探针镜像（按基线 hash 缓存）。"""
    configured = os.environ.get("MUAD_SYSTEM_DEPS_TEST_IMAGE")
    if configured:
        if not image_exists(configured):
            raise RuntimeError(f"MUAD_SYSTEM_DEPS_TEST_IMAGE 不存在: {configured}")
        return configured

    module = load_system_deps_module()
    digest = str(module.baseline_digest(ROOT))
    tag = f"muad-skill-deps-probe:{digest[:12]}"
    if image_exists(tag):
        return tag

    base = _probe_base_image()
    command = [
        _docker(),
        "build",
        "--platform",
        "linux/amd64",
        "--build-arg",
        f"BASE_IMAGE={base}",
        "--build-arg",
        f"PIP_INDEX_URL={os.environ.get('MUAD_SYSTEM_DEPS_PIP_INDEX_URL', 'https://pypi.org/simple')}",
        "--build-arg",
        f"NPM_REGISTRY={os.environ.get('MUAD_SYSTEM_DEPS_NPM_REGISTRY', 'https://registry.npmjs.org/')}",
        "--build-arg",
        f"SKILL_DEPS_SHA256={digest}",
        "--label",
        f"org.muad.skill-deps.sha256={digest}",
        "--label",
        f"org.muad.base-image={base}",
        "-t",
        tag,
        "-f",
        str(PROBE_DOCKERFILE),
        ".",
    ]
    result = _run(command, timeout=BUILD_TIMEOUT_SEC)
    if result.returncode != 0:
        raise RuntimeError(
            "构建系统依赖探针镜像失败\n"
            f"command={command}\nstdout={result.stdout[-4000:]}\nstderr={result.stderr[-4000:]}"
        )
    return tag


def _bwrap_argv(
    command: Sequence[str],
    *,
    mounts: Sequence[tuple[Path, str, str, str]],
    chdir: str,
    extra_env: Sequence[tuple[str, str]] = (),
) -> list[str]:
    argv = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind",
        "/etc",
        "/etc",
        "--ro-bind-try",
        "/opt",
        "/opt",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PATH",
        SANDBOX_PATH,
        "--setenv",
        "HOME",
        "/tmp",
    ]
    for key, value in extra_env:
        argv.extend(("--setenv", key, value))
    for _host, container_path, sandbox_target, mode in mounts:
        argv.extend(("--ro-bind" if mode == "ro" else "--bind", container_path, sandbox_target))
    argv.extend(("--chdir", chdir, "--"))
    argv.extend(command)
    return argv


def run_in_bwrap(
    image: str,
    command: Sequence[str],
    *,
    mounts: Sequence[tuple[Path, str, str, str]] = (),
    chdir: str = "/tmp",
    extra_env: Sequence[tuple[str, str]] = (),
) -> subprocess.CompletedProcess[str]:
    """在真实镜像的 bwrap 中以 UID 10001 运行命令。

    mounts 每项为 ``(宿主机路径, 容器内路径, 沙箱内挂载点, ro|rw)``：Docker 先把
    宿主机路径挂进容器，bwrap 再把容器内路径挂进沙箱。
    """
    argv = _bwrap_argv(command, mounts=mounts, chdir=chdir, extra_env=extra_env)
    docker_command = [
        _docker(),
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--user",
        "10001:10001",
        "--security-opt",
        "seccomp=unconfined",
    ]
    for host, container_path, _sandbox_target, mode in mounts:
        docker_command.extend(("-v", f"{host}:{container_path}:{mode}"))
    docker_command.extend(("--entrypoint", "sh", image, "-c", shlex.join(argv)))
    return _run(docker_command, timeout=RUN_TIMEOUT_SEC)


def run_in_container(image: str, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """以运行时用户（UID 10001）在容器内执行命令，不进入 bwrap。"""
    docker_command = [
        _docker(),
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--user",
        "10001:10001",
        "--entrypoint",
        "sh",
        image,
        "-c",
        shlex.join(command),
    ]
    return _run(docker_command, timeout=RUN_TIMEOUT_SEC)
