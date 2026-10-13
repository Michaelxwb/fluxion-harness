"""Actual history builder, candidate cache, Runner and HTTP request capture."""

import json
from importlib import import_module
from uuid import uuid4

from muad_agent_core.agent import AgentRunner, AgentRunRequest
from muad_agent_core.model import ModelMessage, ModelRole, OpenAICompatibleProvider
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.agent_runtime.memory_confirmation_support import model_endpoint
from tests.agent_runtime.test_memory_redesign_cache import cache


def loader(engine, ctx, *, policy=None, query="你好", task_state_key=None, run_id=None):
    return import_module("muad_agent_runtime.application.memory_context").MemoryContextLoader(
        cache(engine, ctx, run_id=run_id),
        settings=policy or MemoryPolicySettings(),
        query=query,
        task_state_key=task_state_key,
    )


async def request_round(engine, ctx, memory, *, handler=None, extra_history=(), compactor=None):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    builder = DbBackedContextBuilder(session_factory=lambda: factory)
    history = await builder.load_history(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id or uuid4(),
    )
    registry = ToolRegistry()
    calls = []
    if handler is not None:
        registry.register(
            ToolDefinition(
                name="mutate",
                description="mutate",
                effect=ToolEffect.WRITE,
                input_schema={"type": "object"},
                handler=handler,
            )
        )
        calls = [
            {
                "id": "mutate-1",
                "type": "function",
                "function": {"name": "mutate", "arguments": json.dumps({})},
            }
        ]
    async with model_endpoint(calls) as (url, captured):
        provider = OpenAICompatibleProvider(base_url=url, model="probe", api_key=None)
        try:
            runner = AgentRunner(
                provider=provider, registry=registry, context_loader=memory, context_compactor=compactor
            )
            result = await runner.run(
                AgentRunRequest(
                    model_id="probe",
                    instructions="fixed instructions",
                    messages=(*history, *extra_history, ModelMessage(ModelRole.USER, memory.query or "你好")),
                )
            )
        finally:
            await provider.aclose()
    return result, captured


def memory_text(body):
    from muad_agent_core.memory.render import MEMORY_NOTICE

    blocks = [
        item["content"]
        for item in body["messages"]
        if item["role"] == "system" and MEMORY_NOTICE in item["content"]
    ]
    assert len(blocks) <= 1
    return blocks[0] if blocks else None


def executor_request(run, url, is_cancelled):
    from muad_agent_runtime.application.executor import ExecutionDefaults, ExecutorRequest, ExecutorRunContext
    from muad_contracts import ResolvedAgent, ResolvedModel

    return ExecutorRequest(
        agent=ResolvedAgent(
            id=run.agent_id, key="memory", revision=1, instructions="fixed instructions", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid4(), revision=1, model_id="probe", base_url=url, api_key="fixture-credential", params={}
        ),
        input_text="记住蓝色",
        is_cancel_requested=is_cancelled,
        run_context=ExecutorRunContext(
            tenant_id=run.tenant_id, run_id=run.id, conversation_id=run.conversation_id, user_id=run.user_id
        ),
        execution=ExecutionDefaults(memory_policy=MemoryPolicySettings(max_injected_memories=1)),
    )
