"""B-06: replay/eval stay out of the Run request path and use background budget (TASK-004)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUEST_PATH_FILES = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/executor.py",
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/run_service.py",
)
LEARNING_MODULES = ("learning_replay", "learning_eval", "learning_signals")


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return names


def test_b06_replay_is_not_imported_by_the_request_path() -> None:
    for path in REQUEST_PATH_FILES:
        imported = _imported_names(path)
        assert not (imported & set(LEARNING_MODULES)), f"{path.name} imports {imported}"


def test_b06_replay_uses_background_model_budget() -> None:
    from muad_agent_runtime.application import learning_replay

    assert learning_replay.WORKLOAD == "BACKGROUND"
