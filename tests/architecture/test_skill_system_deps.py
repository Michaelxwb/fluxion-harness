"""[S-48][harness-arch#RULE-arch-001] 系统依赖基线静态契约。

只做快速机检；真实镜像/bwrap 的 import/require 由 test_s_48/test_e_17 覆盖。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/skill_system_deps.py"


def _run_validate() -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "validate", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_skill_dependency_baseline_is_valid_and_pinned() -> None:
    payload = _run_validate()
    assert payload["ok"] is True, payload
    assert payload["python"] and payload["node"]
    assert len(str(payload["baseline_sha256"])) == 64


def test_runtime_and_worker_install_skill_deps_during_image_build_only() -> None:
    payload = _run_validate()
    assert payload["ok"] is True, payload
    for name in ("agent-runtime", "agent-worker"):
        text = (ROOT / "build/docker-build" / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "COPY build/docker-build/skill-deps /tmp/skill-deps" in text
        lines = text.splitlines()
        last_user = max(
            index for index, line in enumerate(lines) if line.strip().upper().startswith("USER ")
        )
        after_user = "\n".join(lines[last_user + 1 :])
        for forbidden in ("pip install", "pip3 install", "npm install", "npm ci", "apt-get", "apk add"):
            assert forbidden not in after_user, f"{name}: 运行用户下出现系统依赖安装: {forbidden}"


def test_system_dependency_docs_record_the_production_boundary() -> None:
    text = (ROOT / "docs/operations/system-dependencies.md").read_text(encoding="utf-8")
    for phrase in ("不在运行中的 Pod", "image digest", "申请人", "审批人", "workspace", "配额", "TTL"):
        assert phrase in text, phrase
