"""S-04: task-inner verification loop (TASK-005)."""

from __future__ import annotations

import pytest
from muad_agent_core.agent import (
    AgentRunner,
    AgentRunRequest,
    RunnerVerificationExhausted,
    VerificationStatus,
    run_verification,
)
from muad_agent_core.model import ModelRequest, ModelResponse, ModelRole
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.verification import VerifierRegistry


class ScriptedProvider:
    def __init__(self, script: list[ModelResponse]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("unexpected model call")
        return self._script.pop(0)


def _text(content: str) -> ModelResponse:
    return ModelResponse(content=content, finish_reason="stop", input_tokens=1, output_tokens=1)


async def test_s04_only_verified_candidate_reaches_the_answer() -> None:
    registry = VerifierRegistry()
    seen: list[str] = []

    async def verifier(candidate: str) -> tuple[bool, str]:
        seen.append(candidate)
        return candidate == "final right", "must be the verified answer"

    registry.register("skill:demo", verifier)
    provider = ScriptedProvider([_text("draft wrong"), _text("final right")])
    turns: list[str] = []

    async def on_assistant_turn(message) -> None:
        turns.append(message.content)

    runner = AgentRunner(
        provider=provider,
        registry=ToolRegistry(),
        answer_verifier=registry.resolve("skill:demo"),
        verification_max_attempts=2,
    )
    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i"), on_assistant_turn=on_assistant_turn
    )

    assert result.final_text == "final right"
    assert seen == ["draft wrong", "final right"]
    assert len(provider.requests) == 2
    # 未通过验证的候选不落历史（只有被采纳的回合触发 on_assistant_turn）。
    assert turns == ["final right"]
    second = provider.requests[1]
    assert any(
        message.role is ModelRole.USER and "未通过验证" in message.content
        for message in second.messages
    )


async def test_s04_exhaustion_is_explicit_and_bounded() -> None:
    registry = VerifierRegistry()

    async def verifier(candidate: str) -> tuple[bool, str]:
        return False, "always wrong"

    registry.register("skill:demo", verifier)
    provider = ScriptedProvider([_text("wrong one"), _text("wrong two"), _text("never used")])
    turns: list[str] = []

    async def on_assistant_turn(message) -> None:
        turns.append(message.content)

    runner = AgentRunner(
        provider=provider,
        registry=ToolRegistry(),
        answer_verifier=registry.resolve("skill:demo"),
        verification_max_attempts=2,
    )
    with pytest.raises(RunnerVerificationExhausted):
        await runner.run(
            AgentRunRequest(model_id="m", instructions="i"), on_assistant_turn=on_assistant_turn
        )

    assert len(provider.requests) == 2
    assert turns == []


async def test_s04_run_verification_stops_at_the_attempt_limit() -> None:
    async def verifier(candidate: str) -> tuple[bool, str]:
        return False, f"rejected: {candidate}"

    outcome = await run_verification(["a", "b", "c"], verifier, max_attempts=2)
    assert outcome.status is VerificationStatus.EXHAUSTED
    assert outcome.attempts == 2
    assert outcome.candidate is None

    async def accept_b(candidate: str) -> tuple[bool, str]:
        return candidate == "b", "only b"

    verified = await run_verification(["a", "b", "c"], accept_b, max_attempts=2)
    assert verified.status is VerificationStatus.VERIFIED
    assert verified.candidate == "b"
    assert verified.attempts == 2


def test_s04_registry_rejects_duplicates_and_unknown_families() -> None:
    registry = VerifierRegistry()

    async def verifier(candidate: str) -> tuple[bool, str]:
        return True, ""

    registry.register("skill:demo", verifier)
    with pytest.raises(ValueError):
        registry.register("skill:demo", verifier)
    assert registry.resolve("skill:other") is None
    assert registry.resolve(None) is None
    assert registry.families() == ("skill:demo",)

    loaded = VerifierRegistry()
    loaded.load({"skill:a": verifier, "skill:b": verifier})
    assert loaded.families() == ("skill:a", "skill:b")
