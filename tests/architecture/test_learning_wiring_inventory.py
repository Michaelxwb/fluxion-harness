"""B-01: every learning capability has a production caller (TASK-005)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = (ROOT / "apps", ROOT / "packages", ROOT / "scripts")

#: (定义模块后缀, 需要被外部生产代码引用的符号)
CAPABILITIES = {
    "golden_set": ("GoldenSetExtractor",),
    "experience_candidates": ("ExperienceCandidates",),
    "learning_replay": ("LearningReplay",),
    "learning_activation": ("LearningActivation",),
    "learning_case_runner": ("ProductionCaseRunner",),
}
#: 运行路径上的接线点（符号或方法名）
WIRING_SYMBOLS = ("set_task_family", "learning_of", "frozen_learning_payload", "LearningSupervisor")


def _files() -> list[Path]:
    return [path for base in PRODUCTION_ROOTS for path in base.rglob("*.py")]


def _used_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(alias.name for alias in node.names)
    return names


def test_b01_each_capability_has_a_production_caller() -> None:
    files = _files()
    for module, symbols in CAPABILITIES.items():
        for symbol in symbols:
            callers = [
                path
                for path in files
                if path.stem != module and symbol in _used_names(path)
            ]
            assert callers, f"{symbol} 没有生产调用方（仅测试调用不算）"


def test_b01_runtime_wiring_symbols_are_used_in_production() -> None:
    files = _files()
    for symbol in WIRING_SYMBOLS:
        callers = [path for path in files if symbol in _used_names(path)]
        assert callers, f"{symbol} 未被生产代码使用"


def test_b01_scheduler_is_mounted_in_runtime_main() -> None:
    main = (ROOT / "apps/agent-runtime/src/muad_agent_runtime/main.py").read_text(encoding="utf-8")
    assert "LearningSupervisor(" in main
    assert "learning.run_forever()" in main
    assert "learning_supervisor.close()" in main
