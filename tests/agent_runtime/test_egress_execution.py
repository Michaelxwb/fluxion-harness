"""Runtime producer supervision and unlimited path; not recovery acceptance S-03."""

import uuid

import pytest
from muad_agent_core.agent import AgentRunner
from muad_agent_core.model import ModelResponse
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.executor import AgentRunnerExecutor, ExecutorRequest
from muad_contracts import ResolvedAgent, ResolvedModel


async def never_cancelled():
    return False


def request():
    return ExecutorRequest(
        agent=ResolvedAgent(id=uuid.uuid4(), key="probe", revision=1, instructions="probe"),
        model=ResolvedModel(id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model.test"),
        input_text="probe",
        is_cancel_requested=never_cancelled,
    )


class Provider:
    async def complete(self, request):
        return ModelResponse(content="completed", finish_reason="stop")


class Supervisor:
    called = False

    async def run(self, operation):
        self.called = True
        return await operation()


async def test_runtime_agent_producer_is_inside_source_supervision():
    host = Supervisor()
    executor = AgentRunnerExecutor(
        runner=AgentRunner(provider=Provider(), registry=ToolRegistry()), request=request(), egress=host
    )
    events = [event async for event in executor.run()]
    assert host.called
    assert any(event.type == "runner.outcome" and event.data["final_text"] == "completed" for event in events)


async def test_unlimited_runtime_does_not_require_storage_or_source(monkeypatch):
    from muad_agent_runtime.application import egress_execution as module
    from muad_common import SharedSettings

    monkeypatch.setattr(
        module, "process_settings", lambda: SharedSettings(_env_file=None)
    )
    monkeypatch.setattr(module, "get_session_factory", lambda: pytest.fail("storage constructed"))
    async with module.run_egress(request()) as host:
        assert host is None
