"""创建表单的 predicate 镜像必须与 contracts 受控词表逐项一致。"""

from __future__ import annotations

import re
from pathlib import Path

from muad_contracts.memory_taxonomy import CORE_PREDICATES, SCOPE_DIMENSIONS

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = ROOT / "apps/console-platform/frontend/src/modules/user-identity/memoryTaxonomy.ts"

ENTRY = re.compile(
    r"\{\s*key:\s*'(?P<key>[^']+)',\s*kind:\s*'(?P<kind>[^']+)',"
    r"\s*cardinality:\s*'(?P<cardinality>[^']+)',\s*scopeDimensions:\s*\[(?P<scope>[^\]]*)\],"
    r"\s*labelZh:\s*'(?P<zh>[^']+)',\s*labelEn:\s*'(?P<en>[^']+)'\s*\}"
)
SCOPES = re.compile(r"(?P<dimension>\w+):\s*\[(?P<values>[^\]]*)\]")


def _source() -> str:
    assert TAXONOMY.exists(), f"missing taxonomy mirror: {TAXONOMY}"
    return TAXONOMY.read_text(encoding="utf-8")


def _entries(source: str) -> dict[str, tuple[str, str, tuple[str, ...], str, str]]:
    rows: dict[str, tuple[str, str, tuple[str, ...], str, str]] = {}
    for match in ENTRY.finditer(source):
        dimensions = tuple(
            item.strip().strip("'") for item in match["scope"].split(",") if item.strip()
        )
        rows[match["key"]] = (match["kind"], match["cardinality"], dimensions, match["zh"], match["en"])
    return rows


def test_predicate_mirror_matches_contracts_exactly():
    rows = _entries(_source())
    assert set(rows) == {item.key for item in CORE_PREDICATES}
    for item in CORE_PREDICATES:
        kind, cardinality, dimensions, label_zh, label_en = rows[item.key]
        assert kind == item.kind, item.key
        assert cardinality == item.cardinality, item.key
        assert dimensions == item.scope_dimensions, item.key
        assert label_zh and label_en and label_zh != label_en, item.key


def test_scope_value_mirror_matches_contracts_exactly():
    source = _source()
    block = source.split("SCOPE_VALUES", 1)[1]
    values = {
        match["dimension"]: tuple(
            item.strip().strip("'") for item in match["values"].split(",") if item.strip()
        )
        for match in SCOPES.finditer(block)
    }
    assert values == {name: tuple(options) for name, options in SCOPE_DIMENSIONS.items()}
