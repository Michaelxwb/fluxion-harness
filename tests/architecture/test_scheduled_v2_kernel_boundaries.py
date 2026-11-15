"""共享 Kernel 的依赖边界与端口中立性（TASK-009 建立；TASK-018/019 扩展）。

不得 Mock 的真实边界：**源码静态检查**——读仓库里的真实源文件做 AST 分析，被测对象就是源码
本身（不 import 被测模块、不建库）。

三条断言对应设计 §3.1.2 的"Kernel 完成判据"：
① Kernel 不 import 任一宿主 app、ORM 或渠道适配器；② 挂起端口只依赖中立的
`SuspensionRequest`，Memory 形状留在适配器里；③ 没有任何按宿主类型分支、也没有 stub 收口。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_PACKAGE = ROOT / "packages/agent-core/src/muad_agent_core/agent"

#: 宿主 app 与"库不该碰"的 IO/ORM 依赖：Kernel 只依赖 agent-core 自身 + 共享 contracts。
HOST_APP_MODULES = frozenset(
    (
        "muad_agent_runtime",
        "muad_agent_worker",
        "muad_console_platform",
        "muad_im_gateway",
        "muad_im_connector",
    )
)
IO_MODULES = frozenset(
    ("sqlalchemy", "alembic", "asyncpg", "psycopg", "redis", "httpx", "fastapi", "uvicorn")
)

#: 宿主分类字面量：Kernel 若拿它们做比较，就是在按宿主分支。
HOST_LABELS = frozenset(("runtime", "worker", "agent-runtime", "agent-worker", "RUN", "TASK"))


def kernel_modules() -> list[Path]:
    """Kernel 的全部源文件（TASK-018/019 的适配器检查复用同一份范围）。"""
    return sorted(KERNEL_PACKAGE.rglob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _imported_roots(path: Path) -> set[str]:
    return {module.split(".", 1)[0] for module in _imported_modules(path)}


def _defined_names(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _string_constants(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_kernel_imports_no_host_or_io() -> None:
    for path in kernel_modules():
        overlap = _imported_roots(path) & (HOST_APP_MODULES | IO_MODULES)
        assert not overlap, f"{path.relative_to(ROOT)} imports {sorted(overlap)}"


def test_suspension_port_is_memory_free() -> None:
    """挂起端口只认中立字段：Memory 的请求形状属于适配器，不能再回到 Kernel 里。"""
    suspension = KERNEL_PACKAGE / "suspension.py"
    modules = _imported_modules(suspension)
    assert "muad_contracts.memory" not in modules, "suspension.py imports the memory contracts"
    assert not any("memory" in name.lower() for name in modules), sorted(modules)
    assert not any("memory" in name.lower() for name in _defined_names(suspension)), sorted(
        _defined_names(suspension)
    )
    for path in kernel_modules():
        text = path.read_text(encoding="utf-8")
        assert "MemorySuspendRequest" not in text, f"{path.relative_to(ROOT)} still uses the memory request"


def test_tool_suspension_port_takes_the_neutral_request() -> None:
    tree = _tree(KERNEL_PACKAGE / "suspension.py")
    ports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ToolSuspensionPort"
    ]
    assert ports, "ToolSuspensionPort must live in agent-core"
    methods = {
        node.name: node
        for node in ports[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    annotation = ast.unparse(methods["suspend_request"].returns)  # type: ignore[arg-type]
    assert annotation == "SuspensionRequest | None", annotation
    suspend = methods["suspend"]
    assert ast.unparse(suspend.args.args[-1].annotation) == "RunnerCheckpoint"  # type: ignore[arg-type]


def test_kernel_has_no_host_branching() -> None:
    for path in kernel_modules():
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Compare):
                continue
            compared = {value for value in _string_constants(node) if value in HOST_LABELS}
            assert not compared, f"{path.relative_to(ROOT)} branches on host label {sorted(compared)}"


def test_kernel_has_no_stub_implementation() -> None:
    for path in kernel_modules():
        text = path.read_text(encoding="utf-8")
        assert "NotImplementedError" not in text, f"{path.relative_to(ROOT)} has a not-implemented stub"
        assert "TODO" not in text and "FIXME" not in text, f"{path.relative_to(ROOT)} has a TODO stub"


def test_worker_adapter_does_not_reach_into_runtime_orm() -> None:
    """Worker 走 Runtime 内部 API，不跨 schema 直写 `runtime.*` 的 ORM 模型。"""
    for path in sorted((ROOT / "apps/agent-worker/src").rglob("*.py")):
        modules = _imported_modules(path)
        leaked = {module for module in modules if module.startswith("muad_agent_runtime.infrastructure")}
        assert not leaked, f"{path.relative_to(ROOT)} imports {sorted(leaked)}"
