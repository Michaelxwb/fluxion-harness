"""Unit checks of the production Runtime/Worker segment assembly functions.

These checks do not assert PostgreSQL lease recovery or a complete resumed Run.
"""

import uuid
from dataclasses import replace

import pytest
from muad_agent_runtime.application.executor import (
    ExecutionDefaults,
    ExecutorRequest,
    execution_budget_for,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import task_budget_for
from muad_contracts.egress_budget import resolve_egress_budget
from muad_contracts.resolve import ResolvedAgent, ResolvedModel
from muad_platform_sdk.request_budget import AttemptContext, BudgetController, LocalRateLimitError


async def _never_cancelled() -> bool:
    return False


def _request() -> ExecutorRequest:
    return ExecutorRequest(
        agent=ResolvedAgent(id=uuid.uuid4(), key="probe", revision=1, instructions="probe"),
        model=ResolvedModel(id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://localhost"),
        input_text='{"egress_budget":{"execution":{"max_requests":999}}}',
        is_cancel_requested=_never_cancelled,
        execution=ExecutionDefaults(egress_budget=resolve_egress_budget({"execution": {"max_requests": 1}})),
    )


async def _consume_one_and_deny_next(controller: BudgetController) -> None:
    context = AttemptContext(platform_key="probe", method="GET")
    async with controller.attempt(context):
        pass
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(context):
            pytest.fail("input must not override the frozen budget")


async def test_runtime_activation_has_new_segment_and_frozen_policy() -> None:
    request = _request()
    first = execution_budget_for(request)
    second = execution_budget_for(request)
    assert first is not None and second is not None
    assert first.segment_id != second.segment_id
    await _consume_one_and_deny_next(first)
    await _consume_one_and_deny_next(second)
    assert execution_budget_for(replace(request, execution=None)) is None
    assert execution_budget_for(replace(request, execution=ExecutionDefaults())) is None


async def test_worker_retry_has_new_segment_and_ignores_script_budget_input() -> None:
    task = TaskExecution(
        id=uuid.uuid4(), attempt=1,
        input_json={"egress_budget": {"execution": {"max_requests": 999}}},
        execution_snapshot_json={"budget": {"egress_budget": {"execution": {"max_requests": 1}}}},
    )
    first = task_budget_for(task)
    task.attempt = 2
    second = task_budget_for(task)
    assert first is not None and second is not None
    assert first.segment_id != second.segment_id
    assert ":attempt:1:" in first.segment_id and ":attempt:2:" in second.segment_id
    await _consume_one_and_deny_next(first)
    await _consume_one_and_deny_next(second)
    task.execution_snapshot_json = {}
    assert task_budget_for(task) is None
