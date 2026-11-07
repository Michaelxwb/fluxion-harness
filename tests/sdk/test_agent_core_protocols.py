import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
)
from muad_agent_core.prompt import PromptBuilder, PromptSkill
from muad_agent_core.skill import (
    SkillArtifactResolver,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillExecutor,
)
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import ResolvedSkill, SkillExecutionMode


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
        source="ADMIN",
    )


class DummyModelProvider:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=request.messages[-1].content,
            finish_reason="stop",
            input_tokens=1,
            output_tokens=2,
        )


class DummyPromptBuilder:
    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str:
        catalog = "\n".join(f"- {skill.key}: {skill.name}" for skill in skills)
        return f"{instructions}\n{catalog}"


class DummySkillExecutor:
    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        return SkillExecutionResult(
            status=SkillExecutionStatus.SUCCEEDED,
            result={
                "ready_dir": str(request.ready_dir),
                "input": dict(request.input),
                "source": request.source,
            },
        )


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
    assert parameters == {"self", "request"}

    with pytest.raises(TypeError):
        _instantiate(SkillExecutor)


def _instantiate(cls: type[Any]) -> Any:
    return cls()


async def test_dummy_skill_executor_satisfies_protocol(tmp_path: Path) -> None:
    executor: SkillExecutor = DummySkillExecutor()

    result = await executor.execute(
        SkillExecutionRequest(
            ready_dir=tmp_path,
            input={"question": "hello"},
            timeout_sec=10,
            source="ADMIN",
        )
    )

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {
        "ready_dir": str(tmp_path),
        "input": {"question": "hello"},
        "source": "ADMIN",
    }


def test_real_skill_artifact_cache_satisfies_resolver_protocol(tmp_path: Path) -> None:
    resolver: SkillArtifactResolver = SkillArtifactCache(NfsArtifactStore(tmp_path), tmp_path / "cache")

    assert inspect.iscoroutinefunction(resolver.ensure)
