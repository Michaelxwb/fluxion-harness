"""[S-48] 管理员预装系统依赖后 Skill 直接使用（真实镜像 + 真实 bwrap）。

真实边界：镜像依赖清单 → runtime/worker 镜像构建脚本 → bwrap 系统
site-packages/Node 全局模块 → Skill import/require。不 mock Docker、bwrap 或安装步骤。

验收断言：
- Skill 不需要 workspace 安装即可 import/require；
- Runtime/Worker 的构建输入与基线 hash 一致；
- image digest 和依赖变更记录可查；
- 运行用户不能修改系统 site-packages。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.acceptance.user_skill_sandbox import system_deps_support as support

pytestmark = pytest.mark.integration

ROOT = support.ROOT
RUNTIME_DOCKERFILE = ROOT / "build/docker-build/Dockerfile.agent-runtime"
WORKER_DOCKERFILE = ROOT / "build/docker-build/Dockerfile.agent-worker"
SCRIPT = ROOT / "scripts/skill_system_deps.py"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=support.RUN_TIMEOUT_SEC,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    return payload


def test_s48_baseline_is_declared_and_both_images_install_it() -> None:
    """依赖清单 → runtime/worker Dockerfile 必须在构建期安装同一基线。"""
    baseline = _json_output(_run_cli("validate", "--json"))
    assert baseline["ok"] is True, baseline
    python = baseline["python"]
    node = baseline["node"]
    assert isinstance(python, dict) and python, "基线必须至少登记一个 Python 依赖"
    assert isinstance(node, dict) and node, "基线必须至少登记一个 Node 依赖"

    runtime = RUNTIME_DOCKERFILE.read_text(encoding="utf-8")
    worker = WORKER_DOCKERFILE.read_text(encoding="utf-8")
    for name, text in (("agent-runtime", runtime), ("agent-worker", worker)):
        assert "COPY build/docker-build/skill-deps /tmp/skill-deps" in text, name
        assert "/tmp/skill-deps/install.sh" in text, name
        assert "LABEL org.muad.skill-deps.sha256" in text, name
        assert "ARG SKILL_DEPS_SHA256" in text, name
        # 运行用户切换后不得再安装/修改系统依赖（root 构建阶段允许）。
        lines = text.splitlines()
        last_user = max(
            index for index, line in enumerate(lines) if line.strip().upper().startswith("USER ")
        )
        after_user = "\n".join(lines[last_user + 1 :])
        for forbidden in ("pip install", "pip3 install", "npm install", "npm ci", "apt-get", "apk add"):
            assert forbidden not in after_user, f"{name}: 运行用户下出现系统依赖安装: {forbidden}"


def test_s48_skill_imports_python_and_requires_node_inside_bwrap_without_workspace(
    shared_tmp_path: Path,
) -> None:
    """Skill 包只读挂载、无 workspace 安装时仍能 import/require 预装依赖。"""
    image = support.probe_image()
    baseline = _json_output(_run_cli("validate", "--json"))
    python = baseline["python"]
    node = baseline["node"]
    assert isinstance(python, dict) and isinstance(node, dict)

    python_module, python_version = next(iter(python.items()))
    node_module, node_version = next(iter(node.items()))

    skill = shared_tmp_path / "skill"
    skill.mkdir()
    (skill / "main.py").write_text(
        f"import {python_module}\nprint({python_module}.__version__)\n",
        encoding="utf-8",
    )
    (skill / "main.js").write_text(
        f"console.log(require('{node_module}/package.json').version)\n",
        encoding="utf-8",
    )

    mounts = ((skill, "/probe/skill", "/skill", "ro"),)
    python_result = support.run_in_bwrap(
        image,
        ["python", "/skill/main.py"],
        mounts=mounts,
        chdir="/skill",
    )
    assert python_result.returncode == 0, (
        f"Python Skill 未能直接 import 预装依赖 {python_module}\n"
        f"stdout={python_result.stdout}\nstderr={python_result.stderr}"
    )
    assert python_result.stdout.strip() == str(python_version), python_result.stdout

    node_result = support.run_in_bwrap(
        image,
        ["node", "/skill/main.js"],
        mounts=mounts,
        chdir="/skill",
    )
    assert node_result.returncode == 0, (
        f"Node Skill 未能直接 require 预装依赖 {node_module}\n"
        f"stdout={node_result.stdout}\nstderr={node_result.stderr}"
    )
    assert node_result.stdout.strip() == str(node_version), node_result.stdout


def test_s48_image_digest_and_dependency_record_are_verifiable(shared_tmp_path: Path) -> None:
    """record/verify/change 覆盖 image digest、版本一致性与变更记录字段。"""
    image = support.probe_image()
    runtime_record_path = shared_tmp_path / "agent-runtime.json"
    worker_record_path = shared_tmp_path / "agent-worker.json"

    runtime = _json_output(
        _run_cli(
            "record",
            "--image",
            image,
            "--service",
            "agent-runtime",
            "--output",
            str(runtime_record_path),
            "--json",
        )
    )
    worker = _json_output(
        _run_cli(
            "record",
            "--image",
            image,
            "--service",
            "agent-worker",
            "--output",
            str(worker_record_path),
            "--json",
        )
    )
    for record in (runtime, worker):
        assert str(record["image_id"]).startswith("sha256:")
        assert len(str(record["baseline_sha256"])) == 64
        assert record["site_packages_writable"] is False
        assert record["python"] and record["node"]

    verified = _json_output(
        _run_cli(
            "verify-records",
            "--runtime",
            str(runtime_record_path),
            "--worker",
            str(worker_record_path),
            "--json",
        )
    )
    assert verified["ok"] is True, verified

    tampered = dict(worker)
    tampered["python"] = {"tampered": "0.0.0"}
    worker_record_path.write_text(json.dumps(tampered), encoding="utf-8")
    mismatch = _run_cli(
        "verify-records",
        "--runtime",
        str(runtime_record_path),
        "--worker",
        str(worker_record_path),
        "--json",
    )
    assert mismatch.returncode == 2, mismatch.stdout
    assert "mismatch" in mismatch.stdout.lower() or "不一致" in mismatch.stdout

    change_log = shared_tmp_path / "change-log.jsonl"
    changed = _json_output(
        _run_cli(
            "change",
            "--record",
            str(runtime_record_path),
            "--applicant",
            "release-owner",
            "--approver",
            "security-reviewer",
            "--log",
            str(change_log),
            "--json",
        )
    )
    for key in ("packages", "applicant", "approver", "image", "image_digest", "recorded_at"):
        assert key in changed, changed
    persisted = json.loads(change_log.read_text(encoding="utf-8").splitlines()[0])
    assert persisted["applicant"] == "release-owner"
    assert persisted["approver"] == "security-reviewer"
    assert persisted["image_digest"] == runtime["image_id"]


def test_s48_runtime_user_cannot_modify_system_site_packages() -> None:
    """运行用户（UID 10001）对系统 site-packages / Node 全局目录只读。"""
    image = support.probe_image()
    result = support.run_in_container(
        image,
        [
            "python",
            "-c",
            (
                "import json, os, site;"
                "paths = list(site.getsitepackages()) + ['/usr/local/lib/node'];"
                "print(json.dumps({p: os.access(p, os.W_OK) for p in paths}))"
            ),
        ],
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    writable = json.loads(result.stdout)
    assert writable, writable
    assert all(value is False for value in writable.values()), writable
