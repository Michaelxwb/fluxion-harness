"""[RULE-worker-001] 对照断言：压缩只属于**构建模型请求**的路径，SKILL 路径没有它。

TASK-019 之后 Worker 有两条执行面：

- **AGENTIC**：走中立 Kernel（`agent-core` 的 `AgentRunner`），因此**必然**经过压缩链路——
  压缩器是 `AgentRunner` 组装 `ModelRequest` 的唯一处（design §3.1 ADR-01）；
- **SKILL / REMINDER**：只跑沙箱子进程或固定文本，**不构建模型请求**，一行压缩代码都不该碰。

所以本文件不是"Worker 没有压缩"，而是"压缩只可能出现在 Agentic 面"：SKILL 路径若开始 import
Kernel/压缩链路，这里先红，而不是等到"压缩行为怪怪的"才发现。

真实边界：真实 `apps/agent-worker` 源码（AST 解析，不看注释）+ 真实进程内导入
（`sys.modules` 是导入事实，不是声明）。
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[2] / "apps" / "agent-worker" / "src"

#: 压缩链路与 Kernel：只允许出现在 Agentic 面。
COMPACTION_SURFACE = (
    "muad_agent_core.context.compactor",
    "muad_agent_core.context.summary",
    "muad_agent_core.agent",
)
#: Agentic 面：中立端口的宿主实现与它的执行入口。
AGENTIC_SURFACE = ("application/agentic/", "worker/agentic_", "worker/assembly.py")
#: SKILL 执行链：不建模型请求，因此不碰 Kernel/压缩。
SKILL_PATH_MODULES = (
    "worker/executor.py",
    "worker/dispatch.py",
    "worker/reminder.py",
    "worker/batch",
)


def _imported_modules(path: Path) -> set[str]:
    """AST 解析出真实导入的模块名（注释与字符串里提到名字不算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _worker_sources() -> list[Path]:
    sources = sorted(WORKER_ROOT.rglob("*.py"))
    assert sources, f"没找到 Worker 源码：{WORKER_ROOT}"
    return sources


def _relative(path: Path) -> str:
    return path.relative_to(WORKER_ROOT / "muad_agent_worker").as_posix()


def test_only_the_agentic_surface_imports_the_kernel_and_compaction() -> None:
    """源码面：Kernel/压缩链路只出现在 Agentic 面。"""
    offenders: dict[str, set[str]] = {}
    for source in _worker_sources():
        hits = {
            module
            for module in _imported_modules(source)
            if any(module == prefix or module.startswith(prefix + ".") for prefix in COMPACTION_SURFACE)
        }
        is_agentic = any(_relative(source).startswith(prefix) for prefix in AGENTIC_SURFACE)
        if hits and not is_agentic:
            offenders[_relative(source)] = hits
    assert not offenders, f"非 Agentic 面引入了 Kernel/压缩链路：{offenders}"


def test_skill_path_builds_no_model_request() -> None:
    """对照：SKILL/REMINDER 执行链不 import Kernel、压缩与模型 provider。"""
    seen: set[str] = set()
    for source in _worker_sources():
        relative = _relative(source)
        if not any(relative.startswith(prefix) for prefix in SKILL_PATH_MODULES):
            continue
        modules = _imported_modules(source)
        seen.update(module for module in modules if module.startswith("muad_agent_core"))
        request_prefixes = ("muad_agent_core.agent", "muad_agent_core.context", "muad_agent_core.model")
        forbidden = {module for module in modules if module.startswith(request_prefixes)}
        assert not forbidden, f"{relative} 构建模型请求的依赖：{sorted(forbidden)}"
    assert seen, "SKILL 路径至少应当依赖 Skill 执行面（否则这条断言是空转）"
    assert all(
        module.startswith(("muad_agent_core.skill", "muad_agent_core.sandbox")) for module in seen
    ), f"SKILL 路径依赖了执行之外的 agent-core 面：{sorted(seen)}"


def test_importing_the_worker_pulls_in_the_agentic_compaction() -> None:
    """导入事实：Worker 的 Agentic 装配真的接了压缩链路（不是留着不接的声明）。"""
    importlib.import_module("muad_agent_worker.worker.assembly")
    assert "muad_agent_core.context.compactor" in sys.modules
    assert "muad_agent_core.agent.runner" in sys.modules


def test_runtime_does_construct_the_compactor_so_the_contrast_is_real() -> None:
    """反方向对照：Runtime 侧**确实**挂着压缩器 —— 否则"两边都没有"与"只有 Runtime 有"分不开。"""
    from muad_agent_runtime.application import context_compaction

    assert hasattr(context_compaction, "RuntimeContextCompactor")
    with pytest.raises(ImportError):
        # Worker 侧没有 Runtime 那份实现：Agentic 面的压缩器直接复用 agent-core 的纯逻辑。
        importlib.import_module("muad_agent_worker.application.context_compaction")
