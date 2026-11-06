"""[E-17] workspace 临时安装不作为生产依赖。

真实边界：Pod A 的 workspace 临时 pip 安装 → Pod B/清理后执行同一 Skill。
不 mock pip、文件系统或容器；使用真实镜像与真实 bwrap。

验收断言：
- 新 Pod 不保证存在临时安装的依赖，import 失败可见；
- 生产路径要求管理员预装到系统镜像（基线依赖在空 workspace 仍可用）；
- 临时安装只落在 workspace 内，计入 workspace 配额范围，清理后可被 TTL 删除。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.acceptance.user_skill_sandbox import system_deps_support as support

pytestmark = pytest.mark.integration

ROOT = support.ROOT
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "e17_demo_dep"
POLICY_DOC = ROOT / "docs/operations/system-dependencies.md"


def _skill_dir(root: Path) -> Path:
    skill = root / "skill"
    skill.mkdir(exist_ok=True)
    (skill / "main.py").write_text(
        "import e17_demo_dep\nprint(e17_demo_dep.VALUE)\n",
        encoding="utf-8",
    )
    (skill / "baseline.py").write_text(
        "import tabulate\nprint(tabulate.__version__)\n",
        encoding="utf-8",
    )
    return skill


def _install_into_workspace(image: str, workspace: Path, skill: Path) -> None:
    """Pod A：在真实沙箱内把本地测试包安装到 workspace 的 target 目录。"""
    workspace.mkdir(parents=True, exist_ok=True)
    package_source = workspace / "package-src"
    shutil.copytree(FIXTURE, package_source)
    result = support.run_in_bwrap(
        image,
        [
            "python",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "--target",
            "/workspace/python",
            "/workspace/package-src",
        ],
        mounts=(
            (workspace, "/workspace", "/workspace", "rw"),
            (skill, "/probe/skill", "/probe/skill", "ro"),
        ),
        chdir="/tmp",
    )
    assert result.returncode == 0, (
        "workspace 临时安装应成功\n"
        f"stdout={result.stdout[-4000:]}\nstderr={result.stderr[-4000:]}"
    )


def _run_skill(
    image: str, workspace: Path, skill: Path, script: str = "main.py"
) -> subprocess.CompletedProcess[str]:
    return support.run_in_bwrap(
        image,
        ["python", f"/skill/{script}"],
        mounts=(
            (workspace, "/workspace", "/workspace", "rw"),
            (skill, "/probe/skill", "/skill", "ro"),
        ),
        chdir="/skill",
        extra_env=(("PYTHONPATH", "/workspace/python"),),
    )


def _workspace_bytes(workspace: Path) -> int:
    return sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())


def test_e17_temp_install_does_not_survive_a_new_pod(shared_tmp_path: Path) -> None:
    """Pod A 安装成功；Pod B 使用空 workspace 执行同一 Skill 时 import 失败可见。"""
    image = support.probe_image()
    skill = _skill_dir(shared_tmp_path)
    workspace_a = shared_tmp_path / "workspace-a"

    _install_into_workspace(image, workspace_a, skill)
    assert (workspace_a / "python" / "e17_demo_dep" / "__init__.py").is_file()
    pod_a = _run_skill(image, workspace_a, skill)
    assert pod_a.returncode == 0, f"stdout={pod_a.stdout}\nstderr={pod_a.stderr}"
    assert pod_a.stdout.strip() == "workspace-only"

    workspace_b = shared_tmp_path / "workspace-b"
    workspace_b.mkdir()
    pod_b = _run_skill(image, workspace_b, skill)
    assert pod_b.returncode != 0, "新 Pod 不应凭空拥有 Pod A 的临时安装"
    assert "ModuleNotFoundError" in pod_b.stderr
    assert "e17_demo_dep" in pod_b.stderr

    # 同一 Pod B/空 workspace 中，管理员预装的系统基线仍然可用。
    baseline = _run_skill(image, workspace_b, skill, script="baseline.py")
    assert baseline.returncode == 0, f"stdout={baseline.stdout}\nstderr={baseline.stderr}"
    assert baseline.stdout.strip()


def test_e17_temp_install_is_confined_to_workspace_and_removed_by_cleanup(shared_tmp_path: Path) -> None:
    """临时安装只在 workspace 内；清理 workspace 后依赖随之消失。"""
    image = support.probe_image()
    skill = _skill_dir(shared_tmp_path)
    workspace = shared_tmp_path / "workspace"
    _install_into_workspace(image, workspace, skill)

    installed = workspace / "python" / "e17_demo_dep"
    assert installed.is_dir()
    assert _workspace_bytes(workspace) > 0
    # 依赖没有写进系统 site-packages / Node 全局目录。
    system_check = support.run_in_container(
        image,
        [
            "python",
            "-c",
            (
                "import importlib.util, json, os, site;"
                "paths = list(site.getsitepackages()) + ['/usr/local/lib/node'];"
                "print(json.dumps({"
                "'system_hits': [p for p in paths if os.path.exists(os.path.join(p, 'e17_demo_dep'))],"
                "'writable': {p: os.access(p, os.W_OK) for p in paths}"
                "}))"
            ),
        ],
    )
    assert system_check.returncode == 0, system_check.stderr
    observed = json.loads(system_check.stdout)
    assert observed["system_hits"] == []
    assert all(value is False for value in observed["writable"].values())

    # TTL 清理的最终效果：workspace 被删除后，临时依赖不可恢复。
    shutil.rmtree(workspace)
    assert not workspace.exists()
    fresh = shared_tmp_path / "workspace-after-cleanup"
    fresh.mkdir()
    after_cleanup = _run_skill(image, fresh, skill)
    assert after_cleanup.returncode != 0
    assert "ModuleNotFoundError" in after_cleanup.stderr


def test_e17_policy_requires_image_preinstall_and_counts_workspace_usage() -> None:
    """书面口径：生产依赖走镜像预装；临时安装计入配额并可被 TTL 清理。"""
    assert POLICY_DOC.is_file(), f"缺少系统依赖运维文档: {POLICY_DOC}"
    text = POLICY_DOC.read_text(encoding="utf-8")
    for phrase in (
        "不在运行中的 Pod",
        "workspace",
        "配额",
        "TTL",
        "python-requirements.txt",
        "package.json",
        "image digest",
        "申请人",
        "审批人",
    ):
        assert phrase in text, f"系统依赖文档缺少口径: {phrase}"
