"""Frozen Task egress budget parsing shared by Skill and MCP execution."""

from __future__ import annotations

from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_platform_sdk.shared_redis import MeteringUnavailable
from pydantic import ValidationError

from ...infrastructure.models.task import TaskExecution


def task_frozen_egress_budget(task: TaskExecution) -> EffectiveEgressBudget:
    snapshot = task.execution_snapshot_json
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("budget", {}), dict):
        raise MeteringUnavailable("frozen Task budget invalid")
    try:
        return EffectiveEgressBudget.model_validate(
            snapshot.get("budget", {}).get("egress_budget", {})
        )
    except ValidationError:
        raise MeteringUnavailable("frozen Task egress policy invalid") from None
