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


#: Runtime 侧允许出现 Memory 形状的模块：中立协议的**适配器实现**与它的落库/校验。
MEMORY_REQUEST_MODULES = frozenset(
    (
        "memory_suspension",
        "memory_confirmation",
        "checkpoint",
    )
)


def _referenced_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def test_kernel_references_no_host_entity() -> None:
    """核心循环不认识宿主实体：Run/Task 的 ORM 名一旦出现，就是适配失败。"""
    host_entities = {
        "RunRecord",
        "CanonicalEvent",
        "RunInterrupt",
        "RunContinuation",
        "TaskExecution",
        "TaskEvent",
        "Conversation",
        "tool_call_audit",
    }
    for path in kernel_modules():
        overlap = _referenced_identifiers(_tree(path)) & host_entities
        assert not overlap, f"{path.relative_to(ROOT)} references host entity {sorted(overlap)}"


def test_memory_typed_request_stays_inside_the_runtime_adapter() -> None:
    """Memory 的请求形状只属于适配器：主路径与 Kernel 都只拿到中立的 `SuspensionRequest`。"""
    runtime_src = ROOT / "apps/agent-runtime/src"
    offenders: list[str] = []
    for path in sorted(runtime_src.rglob("*.py")):
        if path.stem in MEMORY_REQUEST_MODULES:
            continue
        if "MemorySuspendRequest" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


WORKER_SRC = ROOT / "apps/agent-worker/src"
#: Worker 侧唯一允许装配 Kernel 的模块：AGENTIC 执行入口（TASK-019）。
WORKER_KERNEL_ENTRY = "application/agentic/run.py"


def _runner_constructions(source_root: Path) -> list[tuple[Path, ast.Call]]:
    calls: list[tuple[Path, ast.Call]] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = _tree(path)
        calls.extend(
            (path, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "AgentRunner")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "AgentRunner")
            )
        )
    return calls


def test_worker_has_exactly_one_kernel_assembly_bound_to_the_neutral_ports() -> None:
    """Worker 只有一处 `AgentRunner(...)`：同一套参数化 Kernel，且四个中立端口都接上。"""
    calls = _runner_constructions(WORKER_SRC)
    assert len(calls) == 1, [f"{path.relative_to(ROOT)}:{call.lineno}" for path, call in calls]
    path, call = calls[0]
    assert path.relative_to(WORKER_SRC / "muad_agent_worker").as_posix() == WORKER_KERNEL_ENTRY
    keywords = {keyword.arg for keyword in call.keywords}
    assert {
        "provider",
        "registry",
        "tool_suspension",
        "runtime_events",
        "context_compactor",
        "tool_round_results",
    } <= keywords, sorted(keywords)


def test_worker_adapter_implements_the_neutral_ports_instead_of_bypassing_them() -> None:
    """Worker 的端口实现真实存在：事件/checkpoint、挂起、整轮外置各一处，不是 stub。"""
    expected = (
        ("application/agentic/checkpoints.py", "class TaskCheckpointStore"),
        ("application/agentic/suspension.py", "class AgenticSuspension"),
        ("application/agentic/artifacts.py", "class TaskArtifactRoundWriter"),
        ("application/agentic/compaction.py", "class TaskContextCompactor"),
        ("application/agentic/model.py", "class CapacityBoundProvider"),
    )
    for relative, marker in expected:
        path = WORKER_SRC / "muad_agent_worker" / relative
        assert marker in path.read_text(encoding="utf-8"), f"{relative} 缺少 {marker}"
