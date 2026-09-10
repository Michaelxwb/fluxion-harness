"""RULE-01 / E-01: Framework Core 必须与具体项目解耦。

两个维度，缺一不可：

1. import 维度 —— Core 不得 import 项目 integration；
2. 字段维度 —— Core 源码不得出现项目专属领域字段。设计 §2.5.2 的 E-01 明确把
   "customer/device 字段"与 "integrations/mss" 并列为必须被 CI 拦住的污染形式，
   只查 import 会漏掉后者（RISK-01 的缓解措施因此形同虚设）。

架构测试只做静态扫描（源码 ast），无 fixture、不连 DB。
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).parents[2]

FORBIDDEN_IMPORT_FRAGMENTS = (
    "integrations.mss",
    "integrations.channels.wecom",
    "wecom",
)

# 项目专属领域字段的标识符片段。Core 保持通用（RULE-01），这些只能出现在
# integrations/*；按完整标识符片段匹配，避免 "compassion" 之类子串误报。
FORBIDDEN_FIELD_TOKENS = frozenset({"customer", "device", "wecom", "mss"})
_TOKEN_SPLIT = re.compile(r"_|(?<=[a-z0-9])(?=[A-Z])")


def _identifiers(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield every identifier-ish name with its line: names, attrs, params,
    keyword args, and string constants that spell a bare identifier."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute):
            yield node.attr, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.keyword) and node.arg:
            yield node.arg, node.lineno
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if text.isidentifier():
                yield text, node.lineno


def _is_project_field(name: str) -> bool:
    return any(token.lower() in FORBIDDEN_FIELD_TOKENS for token in _TOKEN_SPLIT.split(name) if token)


def test_framework_core_does_not_import_project_integrations() -> None:
    root = ROOT / "framework"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for fragment in FORBIDDEN_IMPORT_FRAGMENTS:
            if f"import {fragment}" in text or f"from {fragment}" in text:
                violations.append(f"{path.relative_to(root)} -> {fragment}")
    assert not violations, "Core purity violations:\n" + "\n".join(violations)


def test_framework_core_has_no_project_specific_fields() -> None:
    root = ROOT / "framework"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, lineno in _identifiers(tree):
            if _is_project_field(name):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {name}")
    assert not violations, (
        "Framework Core must stay project-agnostic (RULE-01 / E-01); project-domain "
        "fields belong in integrations/*:\n" + "\n".join(violations)
    )


def test_detector_catches_project_fields() -> None:
    """反例自检：字段维度检测器必须真的能检出，否则本 gate 是空转的。"""
    source = "def f(customer_id, deviceSerial):\n    return customer_id, deviceSerial\n"
    found = {name for name, _ in _identifiers(ast.parse(source)) if _is_project_field(name)}
    assert "customer_id" in found
    assert "deviceSerial" in found


def test_detector_ignores_framework_generic_names() -> None:
    source = "def f(resource_scope_type: str, tenant_id: str) -> None:\n    return None\n"
    found = [name for name, _ in _identifiers(ast.parse(source)) if _is_project_field(name)]
    assert found == [], f"generic framework names must not trip the gate: {found}"
