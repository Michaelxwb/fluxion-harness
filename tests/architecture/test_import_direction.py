from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_MODULES = frozenset(
    ("muad_agent_runtime", "muad_agent_worker", "muad_im_gateway", "muad_console_platform")
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _python_files(relative: str) -> list[Path]:
    return sorted((ROOT / relative).rglob("*.py"))


def test_runtime_and_worker_do_not_import_console() -> None:
    forbidden = "muad_console_platform"
    for relative in ("apps/agent-runtime", "apps/agent-worker"):
        for path in _python_files(relative):
            assert forbidden not in _imported_roots(path), f"{path.relative_to(ROOT)} imports {forbidden}"


def test_packages_do_not_import_app_modules() -> None:
    for path in _python_files("packages"):
        overlap = _imported_roots(path) & APP_MODULES
        assert not overlap, f"{path.relative_to(ROOT)} imports app modules: {sorted(overlap)}"


def test_skill_sdk_does_not_import_runtime_console_or_worker() -> None:
    for path in _python_files("packages/skill-sdk"):
        overlap = _imported_roots(path) & APP_MODULES
        assert not overlap, f"{path.relative_to(ROOT)} imports app modules: {sorted(overlap)}"


def test_import_direction_detects_console_import_violation(tmp_path: Path) -> None:
    violation = tmp_path / "bad_module.py"
    violation.write_text("import muad_console_platform\n", encoding="utf-8")
    roots = _imported_roots(violation)
    assert "muad_console_platform" in roots
    assert roots & APP_MODULES, "dependency-direction check must flag cross-layer app imports"
