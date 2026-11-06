"""S-45/S-46 非干扰守卫的共享数据：需求核心域文件清单 + 需求前生产文件指纹。

`non_interference_baseline.json` 由需求合并基点（`origin/main` 的 merge-base）生成：
任务代码不改 MCP/Memory/Model 时，当前文件 sha256 必须与基线一致；一旦有人改动这些
协议的源文件，S-45 会立刻失败并要求在同一 TASK 里显式说明，而不是悄悄改变语义。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).with_name("fixtures") / "non_interference_baseline.json"

#: 本需求新增/修改、且落在核心域（agent-runtime / agent-worker / agent-core）的文件。
#: S-46 逐一确认它们被渠道中立性扫描覆盖且没有 `ALLOWED_SURFACES` 例外。
REQUIREMENT_CORE_FILES: tuple[str, ...] = (
    "apps/agent-runtime/src/muad_agent_runtime/api/runs.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/attachments/output_service.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/executor.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/task_client.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/workspace_shell.py",
    "apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/__init__.py",
    "apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/workspace.py",
    "apps/agent-runtime/src/muad_agent_runtime/infrastructure/workspace_store.py",
    "apps/agent-runtime/src/muad_agent_runtime/main.py",
    "apps/agent-runtime/src/muad_agent_runtime/metrics.py",
    "apps/agent-worker/src/muad_agent_worker/infrastructure/models/__init__.py",
    "apps/agent-worker/src/muad_agent_worker/infrastructure/models/workspace.py",
    "apps/agent-worker/src/muad_agent_worker/infrastructure/workspace_store.py",
    "apps/agent-worker/src/muad_agent_worker/main.py",
    "apps/agent-worker/src/muad_agent_worker/metrics.py",
    "apps/agent-worker/src/muad_agent_worker/scheduler/service.py",
    "apps/agent-worker/src/muad_agent_worker/worker/executor.py",
    "apps/agent-worker/src/muad_agent_worker/worker/service.py",
    "apps/agent-worker/src/muad_agent_worker/worker/workspace_maintenance.py",
    "packages/agent-core/src/muad_agent_core/sandbox/__init__.py",
    "packages/agent-core/src/muad_agent_core/sandbox/bwrap.py",
    "packages/agent-core/src/muad_agent_core/sandbox/factory.py",
    "packages/agent-core/src/muad_agent_core/sandbox/runner.py",
    "packages/agent-core/src/muad_agent_core/sandbox/spec.py",
    "packages/agent-core/src/muad_agent_core/sandbox/workspace.py",
    "packages/agent-core/src/muad_agent_core/sandbox/workspace_shell.py",
    "packages/agent-core/src/muad_agent_core/skill/__init__.py",
    "packages/agent-core/src/muad_agent_core/skill/contracts.py",
    "packages/agent-core/src/muad_agent_core/skill/executor.py",
    "packages/agent-core/src/muad_agent_core/skill/sandboxed.py",
    "packages/agent-core/src/muad_agent_core/skill/selection.py",
)


def load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def domain_paths(domain: str) -> tuple[str, ...]:
    payload = load_baseline()
    domains = payload.get("domains")
    if not isinstance(domains, dict) or domain not in domains:
        raise AssertionError(f"non-interference baseline has no domain {domain!r}")
    return tuple(domains[domain])


def file_sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def assert_domain_unchanged(domain: str) -> None:
    """该域的所有生产文件必须与需求开始前逐字节一致（无协议语义漂移）。"""
    payload = load_baseline()
    expected: dict[str, str] = payload["sha256"]
    paths = domain_paths(domain)
    assert paths, f"non-interference domain {domain!r} is empty"
    drifted = {}
    for relative in paths:
        path = ROOT / relative
        assert path.is_file(), f"{domain} file disappeared: {relative}"
        assert relative in expected, f"{domain} file is not pinned in baseline: {relative}"
        actual = file_sha256(relative)
        if actual != expected[relative]:
            drifted[relative] = {"expected": expected[relative], "actual": actual}
    assert drifted == {}, (
        f"{domain} protocol files changed since the pre-requirement baseline; "
        f"explain the semantic change instead of silently diverging: {drifted}"
    )
