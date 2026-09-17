import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from muad_agent_core.context import ContextBuilder, ContextInput
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
)
from muad_agent_core.prompt import PromptBuilder, PromptSkill
from muad_agent_core.skill import SkillArtifactResolver, SkillExecutor
from muad_agent_core.tools import ToolDefinition, ToolEffect
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import ResolvedSkill, SkillExecutionMode
from muad_skill_sdk import SkillContext


def _resolved_skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid4(),
        artifact_id=uuid4(),
        key="device-policy-check",
        name="Device Policy Check",
        description="Checks device policy compliance",
        version="1.0.0",
        checksum="sha256:" + "0" * 64,
        storage_key="skills/device-policy-check/1.0.0/skill.zip",
        execution_mode=SkillExecutionMode.SYNC,
    )


class DummyModelProvider:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=request.messages[-1].content,
            finish_reason="stop",
            input_tokens=1,
            output_tokens=2,
        )


class DummyContextBuilder:
    async def build(self, context: ContextInput) -> ModelRequest:
        return ModelRequest(model_id=context.model_id, messages=context.history)


class DummyPromptBuilder:
    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str:
        catalog = "\n".join(f"- {skill.key}: {skill.name}" for skill in skills)
        return f"{instructions}\n{catalog}"


class DummySkillExecutor:
    async def execute(
        self,
        *,
        artifact: ResolvedSkill,
        local_path: Path,
        input_data: Mapping[str, Any],
        context: SkillContext,
    ) -> Mapping[str, Any]:
        return {
            "skill_key": artifact.key,
            "local_path": str(local_path),
            "input": dict(input_data),
            "user_id": context.user.user_id,
        }


async def test_model_messages_and_provider_shape() -> None:
    provider: ModelProvider = DummyModelProvider()
    request = ModelRequest(
        model_id="gpt-4o-mini",
        messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
        temperature=0.2,
    )

    assert inspect.iscoroutinefunction(provider.complete)

    response = await provider.complete(request)
    assert response.content == "hello"
    assert response.finish_reason == "stop"
    assert (response.input_tokens, response.output_tokens) == (1, 2)


async def test_context_builder_shape() -> None:
    builder: ContextBuilder = DummyContextBuilder()
    context = ContextInput(
        model_id="gpt-4o-mini",
        instructions="you are an agent",
        history=(ModelMessage(role=ModelRole.USER, content="hello"),),
    )

    request = await builder.build(context)

    assert request.model_id == "gpt-4o-mini"
    assert request.messages == context.history


def test_prompt_builder_shape() -> None:
    builder: PromptBuilder = DummyPromptBuilder()

    prompt = builder.build(
        instructions="you are an agent",
        skills=(PromptSkill(key="device-policy-check", name="Device Policy Check", description="desc"),),
    )

    assert "you are an agent" in prompt
    assert "- device-policy-check: Device Policy Check" in prompt


def test_skill_executor_is_a_protocol_not_the_old_stub() -> None:
    assert getattr(SkillExecutor, "_is_protocol", False) is True
    assert inspect.iscoroutinefunction(SkillExecutor.execute)

    parameters = set(inspect.signature(SkillExecutor.execute).parameters)
    assert {"artifact", "local_path", "input_data", "context"} <= parameters

    with pytest.raises(TypeError):
        _instantiate(SkillExecutor)


def _instantiate(cls: type[Any]) -> Any:
    return cls()


async def test_dummy_skill_executor_satisfies_protocol(skill_context: SkillContext, tmp_path: Path) -> None:
    executor: SkillExecutor = DummySkillExecutor()

    result = await executor.execute(
        artifact=_resolved_skill(),
        local_path=tmp_path,
        input_data={"question": "hello"},
        context=skill_context,
    )

    assert result["skill_key"] == "device-policy-check"
    assert result["input"] == {"question": "hello"}
    assert result["user_id"] == "user-1"


def test_real_skill_artifact_cache_satisfies_resolver_protocol(tmp_path: Path) -> None:
    resolver: SkillArtifactResolver = SkillArtifactCache(NfsArtifactStore(tmp_path), tmp_path / "cache")

    assert inspect.iscoroutinefunction(resolver.ensure)


def test_tool_definition_is_usable_inside_context_input() -> None:
    context = ContextInput(
        model_id="gpt-4o-mini",
        instructions="you are an agent",
        tools=(
            ToolDefinition(
                name="load_skill",
                description="loads a skill",
                input_schema={"type": "object", "properties": {"skill_key": {"type": "string"}}},
                effect=ToolEffect.READ,
            ),
        ),
    )

    assert context.tools[0].effect is ToolEffect.READ
