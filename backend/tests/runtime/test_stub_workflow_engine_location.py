"""TASK-009（FEAT-08）Stub 移出主模块验收测试（S-07）。"""

from __future__ import annotations

from fluxion.contracts.workflow import WorkflowStartRequest


def test_S07_runtime_workflow_module_has_no_stub() -> None:
    from fluxion.runtime import workflow

    assert not hasattr(workflow, "StubWorkflowEngine")


async def test_S07_stub_moved_and_start_works() -> None:
    from tests.fakes.workflow import StubWorkflowEngine

    stub = StubWorkflowEngine(run_id="r1")
    result = await stub.start(
        WorkflowStartRequest(
            workflow_id="wf-1",
            tenant_id="t1",
            user_id="u1",
            execution_id="exec-1",
            trace_id="trace-1",
            arguments={},
        )
    )
    assert result.run_id == "r1"
