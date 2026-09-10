"""E-02: 已发布 Snapshot 不可原地修改。

Enforcement 在 Repository 层：ServiceRepository 不暴露任何 UPDATE/DELETE
service_release 行的公共路径；唯一写入是 publish() 内的 INSERT。
"""

import ast
from pathlib import Path

REPO = Path(__file__).parents[2] / "adapters" / "postgres" / "service_repository.py"


def test_no_update_or_delete_calls_in_service_repository():
    tree = ast.parse(REPO.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("update", "delete"):
                offenders.append(f"line {node.lineno}: .{func.attr}()")
    assert offenders == []


def test_only_publish_constructs_service_release_rows():
    tree = ast.parse(REPO.read_text(encoding="utf-8"))
    constructors = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ServiceReleaseModel"
    ]
    assert len(constructors) == 1
    publish = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "publish"
    )
    assert min(constructors) > publish.lineno
