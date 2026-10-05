"""Keep the configuration audit tied to real source declarations, without services."""

from __future__ import annotations

import ast
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/configuration-inventory.csv"


def _rows() -> list[dict[str, str]]:
    with CATALOG.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_configuration_inventory_covers_real_python_constants() -> None:
    rows = _rows()
    recorded = {
        (r["path"], int(r["line"]), r["symbol"], r["default"]) for r in rows if r["kind"] == "python-constant"
    }
    actual: set[tuple[str, int, str, str]] = set()
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.strip("_").isupper():
                        actual.add(
                            (str(path.relative_to(ROOT)), node.lineno, target.id, ast.unparse(node.value))
                        )
    assert actual, "The audit must contain real source declarations"
    assert recorded == actual, f"Missing/stale constants: {actual ^ recorded}"


def test_configuration_inventory_covers_shared_settings_and_policy_fields() -> None:
    rows = _rows()
    recorded = {
        (r["path"], int(r["line"]), r["default"])
        for r in rows
        if r["kind"] in {"shared-setting", "policy-field"}
    }
    actual: set[tuple[str, int, str]] = set()
    classes = {
        "SharedSettings",
        "AgentPolicy",
        "BudgetPolicy",
        "EgressPolicy",
        "SnipSettings",
        "ToolResultSettings",
        "MicroSettings",
        "SummarySettings",
        "MemorySettings",
        "CompactionSettings",
    }
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            for cls in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(cls, ast.ClassDef) or cls.name not in classes:
                    continue
                for node in cls.body:
                    if (
                        isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.value is not None
                        and node.target.id != "model_config"
                    ):
                        actual.add((str(path.relative_to(ROOT)), node.lineno, ast.unparse(node.value)))
    assert recorded == actual, f"Missing/stale settings: {actual ^ recorded}"


def test_configuration_inventory_has_valid_locations_and_classification() -> None:
    rows = _rows()
    keys = {(r["path"], r["line"], r["symbol"], r["kind"]) for r in rows}
    assert len(keys) == len(rows), "Duplicate audit declaration"
    for row in rows:
        path = ROOT / row["path"]
        assert path.is_file(), row
        assert 0 < int(row["line"]) <= len(path.read_text(encoding="utf-8").splitlines()), row
        assert row["category"] in {"business", "business-resource", "environment", "code"}, row
        assert row["reason"].strip(), row
        assert ".env" not in path.name, "Do not inventory actual secret values"
    shared = {r["symbol"]: r["category"] for r in rows if r["kind"] == "shared-setting"}
    assert shared["artifact_retention_days"] == "business"
    assert shared["database_url"] == "environment"
    chunks = [r for r in rows if r["symbol"] in {"UPLOAD_CHUNK_BYTES", "MAX_UPLOAD_CHUNKS"}]
    assert {r["symbol"] for r in chunks} == {"UPLOAD_CHUNK_BYTES", "MAX_UPLOAD_CHUNKS"}
    assert all(r["category"] == "code" for r in chunks)
