"""B-01 / RULE-P3-01（unit）：WorkflowAdapter 无 durable state 恒等不变量。

真实边界：真实 WorkflowAdapter 实例（经真实 runtime_context 执行）+ 源码 AST 扫描
（不 mock）。断言：
- `local_durable_state_count` 恒等 0（执行前后，多次执行）；
- runtime 包 workflow 模块无模块级可变 run 状态容器（durable state 归 Workflow
  Engine / PG，不落 Runtime 进程内存，rule 13）；
- `WorkflowEngine` Protocol 7 成员齐备（含 await_result / get_execution_history）；
- `DbosWorkflowEngine` 结构化实现 Protocol；`WorkflowStartRequest` 携带 pinned refs。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.runtime_helpers import runtime_context

from fluxion.runtime.workflow import (
    StubWorkflowEngine,
    WorkflowAdapter,
    WorkflowEngine,
    WorkflowPinnedRef,
    WorkflowStartRequest,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_DIR = _BACKEND_ROOT / "src" / "fluxion" / "runtime"
_PROTOCOL_MEMBERS = (
    "start",
    "resume",
    "signal",
    "cancel",
    "get_status",
    "await_result",
    "get_execution_history",
)


async def test_adapter_local_durable_state_count_always_zero() -> None:
    context, _runtime = await runtime_context()
    adapter = WorkflowAdapter(workflow_id="wf-b01", engine=StubWorkflowEngine(run_id="run-1"))
    assert adapter.local_durable_state_count == 0
    assert adapter.descriptor.tool_id == "workflow.wf-b01.start"
    for _ in range(3):
        result = await adapter.execute(context, {"payload": "x"})
        assert result.run_id == "run-1"
        assert adapter.local_durable_state_count == 0


def test_protocol_declares_seven_members() -> None:
    for member in _PROTOCOL_MEMBERS:
        assert member in WorkflowEngine.__protocol_attrs__, member


def test_dbos_engine_implements_protocol() -> None:
    from fluxion.runtime.workflow_dbos import DbosWorkflowEngine

    assert issubclass(DbosWorkflowEngine, WorkflowEngine)


def test_start_request_carries_pinned_refs() -> None:
    request = WorkflowStartRequest(
        workflow_id="wf-1",
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id="exec-1",
        trace_id="trace-1",
        arguments={},
        pinned=(
            WorkflowPinnedRef(kind="workflow", id="wf-1", version="2"),
            WorkflowPinnedRef(kind="skill", id="greet", version="1"),
        ),
    )
    assert request.pinned[0].kind == "workflow"
    assert request.pinned[1].id == "greet"


def test_runtime_workflow_modules_have_no_module_level_run_state() -> None:
    """模块级可变容器（dict/list/set 字面量或构造调用）= 进程内 durable state。"""
    for name in ("workflow.py", "workflow_dbos.py"):
        path = _RUNTIME_DIR / name
        assert path.is_file(), f"runtime/{name} 应存在（TASK-001 落点）"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            value = node.value if isinstance(node, ast.Assign) else node.value
            if value is None:
                continue
            if isinstance(value, ast.Dict | ast.List | ast.Set):
                raise AssertionError(f"{name} 模块级可变容器违反 RULE-P3-01: {ast.dump(node)[:120]}")
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"dict", "list", "set"}
            ):
                raise AssertionError(f"{name} 模块级可变容器违反 RULE-P3-01: {ast.dump(node)[:120]}")
