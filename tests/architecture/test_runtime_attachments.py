"""Attachment services must not depend on their callers or HTTP entrypoints."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "apps/agent-runtime/src"
PACKAGE = SOURCE / "muad_agent_runtime/application/attachments"
FORBIDDEN = (
    "muad_agent_runtime.api",
    "muad_agent_runtime.bootstrap",
    "muad_agent_runtime.application.executor",
    "muad_agent_runtime.application.run_service",
    "muad_agent_runtime.application.context_builder",
)


def _imports(path: Path, source_root: Path = SOURCE) -> set[str]:
    package = ".".join(path.relative_to(source_root).parts[:-1])
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            imported.add(resolve_name(name, package) if node.level else name)
    return imported


def test_attachment_services_do_not_import_orchestration_or_entrypoints() -> None:
    paths = sorted(PACKAGE.rglob("*.py"))
    assert paths, "attachment application package is missing"
    for path in paths:
        for imported in _imports(path):
            assert not any(
                imported == prefix or imported.startswith(prefix + ".") for prefix in FORBIDDEN
            ), f"{path.relative_to(ROOT)} imports its caller: {imported}"


def test_dependency_guard_resolves_relative_imports(tmp_path: Path) -> None:
    sample = tmp_path / "muad_agent_runtime/application/attachments/example.py"
    sample.parent.mkdir(parents=True)
    sample.write_text("from ..executor import AgentRunnerExecutor\n", encoding="utf-8")
    imports = _imports(sample, source_root=tmp_path)
    assert "muad_agent_runtime.application.executor" in imports
    assert imports & set(FORBIDDEN), "relative imports must not bypass dependency checks"
