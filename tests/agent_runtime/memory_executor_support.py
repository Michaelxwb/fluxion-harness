"""Real default executor, HTTP model, round audits and canonical event persistence."""

import json
from dataclasses import replace

from muad_agent_runtime.application import executor as executor_module
from muad_agent_runtime.application.run_events import EventWriter

from tests.agent_runtime.memory_confirmation_support import model_endpoint
from tests.agent_runtime.memory_context_support import executor_request


def tool_call(name, arguments, call_id="memory-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


async def execute(
    factory,
    run,
    identity,
    submission_id,
    calls,
    monkeypatch,
    *,
    policy=None,
    config=None,
    history=(),
    resume_ref=None,
):
    from muad_agent_runtime.application.executor import ExecutionDefaults

    monkeypatch.setattr(executor_module, "get_session_factory", lambda: factory)

    async def cancelled():
        return False

    events = []
    async with model_endpoint(calls) as (url, captured):
        request = executor_request(run, url, cancelled)
        request = replace(
            request,
            execution_identity=identity,
            history=history,
            run_context=replace(
                request.run_context, submission_id=submission_id, memory_resume_ref=resume_ref
            ),
        )
        if policy is not None:
            request = replace(request, execution=ExecutionDefaults(memory_policy=policy))
        if config is not None:
            request = replace(request, agent=request.agent.model_copy(update={"runtime_config": config}))
        executor = await executor_module.default_executor_factory(request)
        async for event in executor.run():
            events.append(event)
            kind = {"assistant.turn": "ASSISTANT_TURN", "tool.completed": "TOOL_CALL"}.get(event.type)
            if kind is not None:
                async with factory.begin() as session:
                    await EventWriter(session).append(
                        tenant_id=run.tenant_id,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        submission_id=submission_id,
                        event_type=kind,
                        payload=event.data,
                    )
    return events, captured
