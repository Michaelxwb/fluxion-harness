"""Phase 3 workflow 架构守护（TASK-001；directory rule / RULE-P3-01）。

- Kernel 只依赖 Contract：kernel/ 禁止 import dbos（DBOS 是 infra behind Contract）；
- `runtime/workflow_dbos.py` 归属 runtime 包（与 Protocol 同包，镜像 RegistryStore
  adapter 模式）；
- 依赖方向：runtime 不 import services；services 不 import runtime 内部实现；
  api 不 import runtime 内部实现（api → services → contracts）。
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_FLUXION_ROOT = _BACKEND_ROOT / "src" / "fluxion"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_kernel_does_not_import_dbos() -> None:
    kernel_dir = _FLUXION_ROOT / "kernel"
    for path in sorted(kernel_dir.glob("*.py")):
        assert not any(m.split(".")[0] == "dbos" for m in _imported_modules(path)), path


def test_workflow_dbos_engine_lives_in_runtime_package() -> None:
    path = _FLUXION_ROOT / "runtime" / "workflow_dbos.py"
    assert path.is_file(), "runtime/workflow_dbos.py 应存在（TASK-001 落点）"
    modules = _imported_modules(path)
    assert any(m.split(".")[0] == "dbos" for m in modules), "workflow_dbos.py 应封装 DBOS SDK"
    # 引擎实现紧贴其 Contract（ADR-WF-001），但不得反向侵入 services/api
    assert not any(m.startswith("fluxion.services") for m in modules)
    assert not any(m.startswith("fluxion.api") for m in modules)


def test_runtime_workflow_modules_do_not_import_services_or_api() -> None:
    for name in ("workflow.py", "workflow_dbos.py"):
        path = _FLUXION_ROOT / "runtime" / name
        if not path.is_file():
            continue
        modules = _imported_modules(path)
        assert not any(m.startswith("fluxion.services") for m in modules), path
        assert not any(m.startswith("fluxion.api") for m in modules), path


def test_graph_step_executors_have_no_fluxion_retry_loop() -> None:
    """RULE-P3-04 / RISK-P3-02：step 级 durable retry 归 DBOS，解释器禁 double retry。

    `@DBOS.step(retries_allowed=True, ...)` 是 `_run_node` 唯一重试声明；step
    executor 函数体（`_run_node`/`_dispatch_node`/`_execute_capability`/
    `_execute_agent`/`_route_switch`）内不得出现重试循环形态——`while` 循环或
    `for ... in range(...)` 即 Fluxion 层重试，与 DBOS durable retry 叠加
    （E-03 由真实 step retry 证明业务写不重复；本测试为 AST 静态守护）。
    普通数据迭代（如 switch cases 遍历）不属重试，不命中。
    """

    def _is_retry_loop(node: ast.AST) -> bool:
        if isinstance(node, ast.While):
            return True
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
            return isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range"
        return False

    path = _FLUXION_ROOT / "runtime" / "workflow_graph.py"
    if not path.is_file():
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    executor_names = {
        "_run_node",
        "_dispatch_node",
        "_execute_capability",
        "_execute_agent",
        "_route_switch",
    }
    for name in executor_names:
        fn = funcs.get(name)
        if fn is None:
            continue
        loops = [node for node in ast.walk(fn) if _is_retry_loop(node)]
        assert not loops, f"{name} 含重试循环（疑似 Fluxion 层重试，RULE-P3-04 禁 double retry）"


def test_workflow_projection_service_and_api_respect_dependency_direction() -> None:
    """TASK-008 落点预守护：api/workflow.py → services/workflow_projection.py → contracts。

    投影服务只读投影表 + 契约模型，不得直连 runtime 内部实现（Console 只读投影，
    Runtime 边界不内侵）；execution history 读取下沉 services（P0-3，`api/console.py`
    同样不得 import `fluxion.runtime.*`）。
    """
    guarded = [
        _FLUXION_ROOT / "services" / "workflow_projection.py",
        _FLUXION_ROOT / "api" / "workflow.py",
        _FLUXION_ROOT / "api" / "console.py",
    ]
    for path in guarded:
        if not path.is_file():
            continue
        modules = _imported_modules(path)
        assert not any(m.startswith("fluxion.runtime") for m in modules), path
    api = _FLUXION_ROOT / "api" / "workflow.py"
    if api.is_file():
        modules = _imported_modules(api)
        assert any(m.startswith("fluxion.services") for m in modules), api
