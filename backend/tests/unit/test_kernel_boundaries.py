from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fluxion.runtime.resolver import LATEST_PUBLISHED, _parse_selector


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def _assert_no_forbidden_imports(root: Path, forbidden: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        for module in _imports(path):
            if module.startswith(forbidden):
                violations.append(f"{path}:{module}")
    assert not violations, f"forbidden dependencies: {violations}"


def test_kernel_only_depends_on_contract_boundaries() -> None:
    _assert_no_forbidden_imports(
        Path("backend/src/fluxion/kernel"),
        ("fluxion.api", "fluxion.services", "fluxion.registry", "fluxion.plugins"),
    )


def test_runtime_does_not_depend_on_console_implementation() -> None:
    _assert_no_forbidden_imports(
        Path("backend/src/fluxion/runtime"),
        ("fluxion.api.console", "fluxion.services.console_app"),
    )


@pytest.mark.parametrize(
    ("value", "resource_id", "selector"),
    [
        ("search", "search", LATEST_PUBLISHED),
        ("search@1", "search", "1"),
        ("search@", "search", LATEST_PUBLISHED),
        ("search@ ", "search", LATEST_PUBLISHED),
    ],
)
def test_parse_selector_trailing_at_falls_back_to_latest_published(
    value: str, resource_id: str, selector: str
) -> None:
    parsed = _parse_selector(value)
    assert parsed.resource_id == resource_id
    assert parsed.selector == selector
