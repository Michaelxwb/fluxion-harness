"""Unit checks of the production Runtime/Worker segment assembly functions.

These checks do not assert PostgreSQL lease recovery or a complete resumed Run.
"""

import uuid
from dataclasses import replace

from muad_agent_runtime.application.executor import (
    ExecutionDefaults,
    ExecutorRequest,
    execution_defaults_for,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.egress_execution import task_frozen_egress_budget
from muad_contracts.egress_budget import resolve_egress_budget
from muad_contracts.resolve import ResolvedAgent, ResolvedModel


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


def test_runtime_reads_frozen_policy_and_ignores_input_budget() -> None:
    request = _request()
    first = execution_defaults_for(request).egress_budget
    second = execution_defaults_for(request).egress_budget
    assert first == second
    assert first.execution.max_requests == 1
    assert execution_defaults_for(replace(request, execution=None)).egress_budget.is_unlimited
    assert execution_defaults_for(replace(request, execution=ExecutionDefaults())).egress_budget.is_unlimited


def test_worker_freezes_snapshot_policy_and_ignores_script_budget_input() -> None:
    task = TaskExecution(
        id=uuid.uuid4(), attempt=1,
        input_json={"egress_budget": {"execution": {"max_requests": 999}}},
        execution_snapshot_json={"budget": {"egress_budget": {"execution": {"max_requests": 1}}}},
    )
    first = task_frozen_egress_budget(task)
    task.attempt = 2
    second = task_frozen_egress_budget(task)
    assert first == second
    assert first.execution.max_requests == 1
    task.execution_snapshot_json = {}
    assert task_frozen_egress_budget(task).is_unlimited
