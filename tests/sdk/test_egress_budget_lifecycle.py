"""真实子进程与进程内预算段生命周期（TASK-005 E-02/E-03）。"""

from pathlib import Path

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_contracts.egress_budget import EffectiveEgressBudget, RequestLimits, SkillBudget
from muad_platform_sdk.request_budget import AttemptContext, LocalRateLimitError, create_budget_controller


def _policy(total: int) -> EffectiveEgressBudget:
    return EffectiveEgressBudget(
        execution=RequestLimits(max_requests=total),
        skills={"probe": SkillBudget(total=RequestLimits(max_requests=total))},
    )


def _script(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")


async def test_budget_denial_signals_parent_and_kills_skill_group(tmp_path: Path) -> None:
    _script(tmp_path)
    controller = create_budget_controller(_policy(1), segment_id="segment-a")
    assert controller is not None
    invocation = controller.invocation("probe", "invocation-a")
    context = AttemptContext(platform_key="mss", method="GET", path="/assets")
    async with controller.attempt(context) as lease:
        lease.outcome = "success"
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(
            AttemptContext(platform_key="mss", method="GET", path="/assets", invocation=invocation)
        ):
            pass
    with pytest.raises(SkillExecutionError, match="LOCAL_BUDGET_EXHAUSTED"):
        await ScriptSkillExecutor().execute(
            SkillExecutionRequest(
                ready_dir=tmp_path,
                input={},
                timeout_sec=10,
                budget_exhausted_event=invocation.exhausted,
            )
        )


async def test_new_process_segment_starts_a_new_local_counter() -> None:
    for segment in ("segment-a", "segment-b"):
        controller = create_budget_controller(_policy(1), segment_id=segment)
        assert controller is not None
        context = AttemptContext(platform_key="mss", method="GET", path="/assets")
        async with controller.attempt(context):
            pass
        with pytest.raises(LocalRateLimitError):
            async with controller.attempt(context):
                pass
