"""[RULE-worker-001] 对照断言：Worker 路径**不引入压缩**。

本需求把压缩放在 `AgentRunner` 组装 `ModelRequest` 的唯一处（design §3.1 ADR-01），而
`AgentRunner` 全仓只有 `apps/agent-runtime` 一处构造；Worker 只执行 Skill 脚本、**不构建模型请求**。
所以"跨 Pod 一致"不经过 Worker —— 这里用**对照断言**把这个事实钉住：不是"Worker 也压缩得对"，
而是"Worker 根本不在这条链路上"。

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

#: 压缩链路的三个入口：端口/纯逻辑（agent-core）与运行时适配器（agent-runtime）。
COMPACTION_SURFACE = (
    "muad_agent_core.context.compactor",
    "muad_agent_core.context.summary",
    "muad_agent_core.agent.runner",
    "muad_agent_runtime.application.context_compaction",
)

#: Worker 真正依赖的 agent-core 面：只到 Skill 执行。
WORKER_ALLOWED_CORE_IMPORTS = ("muad_agent_core.skill",)


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


def test_worker_source_never_imports_the_compaction_surface() -> None:
    """源码面：`apps/agent-worker` 一行都不许 import 压缩链路。"""
    offenders: dict[str, set[str]] = {}
    for source in _worker_sources():
        hits = {
            module
            for module in _imported_modules(source)
            if any(module.startswith(prefix) for prefix in COMPACTION_SURFACE)
        }
        if hits:
            offenders[str(source.relative_to(WORKER_ROOT))] = hits
    assert not offenders, f"Worker 引入了压缩链路：{offenders}"


def test_worker_core_surface_is_skill_only() -> None:
    """对照：Worker 依赖的 agent-core 面**只有** Skill 执行。

    这条是上面那条的"非空转"保证 —— 若哪天 Worker 开始构建模型请求（比如共用 runner），
    这里会先红，而不是等到"压缩行为怪怪的"才发现。
    """
    seen = {
        module
        for source in _worker_sources()
        for module in _imported_modules(source)
        if module.startswith("muad_agent_core")
    }
    assert seen, "Worker 至少应当依赖 Skill 执行面（否则这条断言是空转）"
    unexpected = {
        module
        for module in seen
        if not any(module.startswith(allowed) for allowed in WORKER_ALLOWED_CORE_IMPORTS)
    }
    assert not unexpected, f"Worker 依赖了 Skill 执行之外的 agent-core 面：{unexpected}"


def test_importing_the_worker_does_not_pull_in_the_compactor() -> None:
    """导入事实：真的 import 一次 Worker 入口，压缩模块不得因它而进入 `sys.modules`。"""
    before = set(sys.modules)
    importlib.import_module("muad_agent_worker.main")
    loaded = {
        module
        for module in set(sys.modules) - before
        if any(module.startswith(prefix) for prefix in COMPACTION_SURFACE)
    }
    assert not loaded, f"导入 Worker 顺带把压缩链路拉进来了：{loaded}"


def test_runtime_does_construct_the_compactor_so_the_contrast_is_real() -> None:
    """反方向对照：Runtime 侧**确实**挂着压缩器 —— 否则"Worker 没有"只是两边都没有。"""
    from muad_agent_runtime.application import context_compaction

    assert hasattr(context_compaction, "RuntimeContextCompactor")
    with pytest.raises(ImportError):
        # Worker 侧不存在对应的适配器：这条断言让"两边都没有"与"只有 Runtime 有"区分得开
        importlib.import_module("muad_agent_worker.application.context_compaction")
