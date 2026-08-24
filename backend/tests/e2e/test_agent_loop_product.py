from __future__ import annotations

import json
from typing import cast

import pytest
from tests.product_wire import (
    openai_final_response,
    openai_tool_call_response,
    openai_wire_server,
)
from tests.runtime_helpers import publish_resource

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    HookScope,
    TypedEventBus,
)
from fluxion.plugins.model_provider import (
    ModelProviderRegistry,
    OpenAICompatibleHTTPModelProvider,
)
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime import AgentRuntime
from fluxion.runtime.context import RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver
from fluxion.runtime.tools import ToolDescriptor, ToolRuntime
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest


def _model_registry(base_url: str) -> ModelProviderRegistry:
    registry = ModelProviderRegistry()
    registry.register(
        "wire",
        OpenAICompatibleHTTPModelProvider(
            provider_id="wire",
            api_base_url=base_url,
            model="fixture-model",
            timeout_seconds=2,
            max_retries=0,
        ),
    )
    return registry


async def _publish_profile(
    store: RegistryStore,
    *,
    allowed_skills: list[str] | None = None,
) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={
            "prompt": "你是 Fluxion 助手。",
            "model_policy": {
                "provider": "wire",
                "model": "fixture-model",
                "timeout_ms": 2_000,
                "max_rounds": 4,
                "deadline_ms": 5_000,
            },
            "allowed_skills": allowed_skills or [],
            "allowed_tools": ["lookup"],
        },
    )


@pytest.mark.asyncio
async def test_S_P13_01_model_tool_result_returns_to_second_real_http_call(
    sqlite_store: RegistryStore,
) -> None:
    async with openai_wire_server(
        [openai_tool_call_response(), openai_final_response("来自 lookup 的最终答案")]
    ) as wire:
        await _publish_profile(sqlite_store)
        tools = ToolRuntime()
        tool_invocations: list[dict[str, object]] = []
        tools.register(
            ToolDescriptor(
                tool_id="lookup",
                capability_id="cap.lookup",
                name="lookup",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            lambda _context, arguments: tool_invocations.append(arguments)
            or {"answer": "Fluxion registry result"},
        )
        hook_calls: list[str] = []
        event_bus = TypedEventBus()
        event_bus.register(
            HookRegistration(
                registration_id="test-before-tool",
                event_type=BeforeToolCallPayload,
                priority=10,
                timeout_ms=500,
                fail_policy=FailPolicy.FAIL_CLOSED,
                scope=HookScope.GLOBAL,
                handler=lambda payload: hook_calls.append(payload.tool_id),
            )
        )
        runtime = RuntimeApplicationService(
            sqlite_store,
            model_providers=_model_registry(wire.base_url),
            tool_runtime=tools,
            event_bus=event_bus,
        )

        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="查询 Fluxion",
            )
        )

        assert len(wire.requests) == 2
        second_messages = cast(list[dict[str, object]], wire.requests[1]["messages"])
        assert second_messages[-2]["role"] == "assistant"
        assert second_messages[-2]["tool_calls"] == [
            {
                "id": "call-lookup-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"query":"fluxion"}'},
            }
        ]
        assert second_messages[-1] == {
            "role": "tool",
            "content": '{"answer":"Fluxion registry result"}',
            "tool_call_id": "call-lookup-1",
            "name": "lookup",
        }
        assert tool_invocations == [{"query": "fluxion"}]
        assert hook_calls == ["lookup"]
        assert result.output == "来自 lookup 的最终答案"
        assert result.tool_results[0]["tool_id"] == "lookup"
        trace = await runtime.trace_store.get(result.trace_id)
        assert trace is not None
        assert [event.name for event in trace.events].count("model.completed") == 2
        assert any(event.name == "tool.completed" for event in trace.events)


@pytest.mark.asyncio
async def test_S_P13_02_published_skill_instructions_are_fixed_in_snapshot_and_prompt(
    sqlite_store: RegistryStore,
) -> None:
    async with openai_wire_server([openai_final_response("按 Skill 回答")]) as wire:
        await publish_resource(
            sqlite_store,
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="concise",
            version="1",
            spec={
                "name": "concise",
                "description": "简洁回答",
                "instructions": "回答必须以 SKILL-V1 开头。",
                "allowed_tools": ["lookup"],
            },
        )
        await _publish_profile(sqlite_store, allowed_skills=["concise@1"])
        runtime = AgentRuntime(
            snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
            memory_store=InMemorySessionMemoryStore(),
            model_providers=_model_registry(wire.base_url),
        )
        context = await runtime.start_execution(
            RequestContext(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
            )
        )
        await publish_resource(
            sqlite_store,
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="concise",
            version="2",
            spec={
                "name": "concise",
                "description": "已更新",
                "instructions": "回答必须以 SKILL-V2 开头。",
                "allowed_tools": ["lookup"],
            },
        )

        result = await runtime.run_step(context, "说明当前版本")

        messages = cast(list[dict[str, object]], wire.requests[0]["messages"])
        system_messages = [message["content"] for message in messages if message["role"] == "system"]
        assert context.snapshot.skill_versions == {"concise": "1"}
        assert context.snapshot.skill_instructions == {
            "concise": "回答必须以 SKILL-V1 开头。"
        }
        assert system_messages == [
            "你是 Fluxion 助手。\n\n## Skill: concise\n回答必须以 SKILL-V1 开头。"
        ]
        assert "SKILL-V2" not in json.dumps(messages, ensure_ascii=False)
        assert result.output == "按 Skill 回答"
