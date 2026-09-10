"""E-02: 已发布 Snapshot 不可被原地修改。

Enforcement 在 gate 层：全仓不得构造任何针对 ``service_release`` 的
UPDATE/DELETE 语句，``ServiceRepository.publish`` 的 INSERT 是唯一写入。

设计 v1.2 明确 DB 层不加 trigger（"单一写入者约定由 gate 保证"），因此本 gate
必须覆盖**函数形式** ``update(Model)`` / ``delete(Model)``——只匹配属性形式会漏掉
``session.execute(update(...))`` 这条真实绕过路径。

架构测试只做静态扫描（源码 ast），无 fixture、不连 DB。
"""

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCAN_DIRS = ("adapters", "framework", "apps")
TARGET = "ServiceReleaseModel"
STATEMENT_FORMS = frozenset({"update", "delete"})
METHOD_FORMS = frozenset({"update", "delete"})


def _source_files() -> Iterator[Path]:
    for dirname in SCAN_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _decorator_calls(tree: ast.AST) -> set[int]:
    """Id of Call nodes used as decorators, e.g. ``@router.delete("/x")``."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        for decorator in getattr(node, "decorator_list", []):
            for inner in ast.walk(decorator):
                ids.add(id(inner))
    return ids


def scan(tree: ast.AST, label: str) -> list[str]:
    """Return every release-mutating call found in ``tree``."""
    decorators = _decorator_calls(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in decorators:
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in STATEMENT_FORMS:
            if any(isinstance(arg, ast.Name) and arg.id == TARGET for arg in node.args):
                hits.append(f"{label}:{node.lineno}: {func.id}({TARGET})")
        elif isinstance(func, ast.Attribute) and func.attr in METHOD_FORMS:
            hits.append(f"{label}:{node.lineno}: .{func.attr}()")
    return hits


def test_no_release_mutation_statements_anywhere() -> None:
    offenders: list[str] = []
    for path in _source_files():
        offenders.extend(scan(ast.parse(path.read_text(encoding="utf-8")), str(path.relative_to(ROOT))))
    assert offenders == [], (
        "service_release is append-only; these calls can mutate published snapshots:\n" + "\n".join(offenders)
    )


def test_only_service_repository_constructs_release_rows() -> None:
    offenders: list[str] = []
    constructed = 0
    for path in _source_files():
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == TARGET:
                constructed += 1
                if rel != "adapters/postgres/service_repository.py":
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"only ServiceRepository.publish may insert releases: {offenders}"
    assert constructed == 1, f"expected exactly one release constructor, found {constructed}"


def test_scanner_catches_the_execute_update_bypass() -> None:
    """反例自检：属性形式匹配不到的绕过路径必须被函数形式检出。"""
    source = "async def f(s):\n    await s.execute(update(ServiceReleaseModel).where(1).values(a=1))\n"
    assert scan(ast.parse(source), "synthetic.py") != []


def test_scanner_catches_orm_attribute_mutation() -> None:
    source = "def f(session):\n    session.query(ServiceReleaseModel).update({'a': 1})\n"
    assert scan(ast.parse(source), "synthetic.py") != []


def test_scanner_ignores_route_decorators() -> None:
    """``@router.delete(...)`` 是路由声明，不是 ORM 变更，不得误报。"""
    source = "def f(router):\n    @router.delete('/x')\n    def handler():\n        return None\n"
    assert scan(ast.parse(source), "synthetic.py") == []
