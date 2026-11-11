"""Host logical operation keys, independent of attempts, leases and directories.

These keys name an operation within its authenticated execution. They do not
authenticate a caller or grant access to an execution/invocation registry row.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from .egress_budget import EffectiveEgressBudget, RequestLimits, TargetRule
from .shared_egress import FrozenIdentityBudgets, FrozenScopeBudget


def egress_operation_key(kind: Literal["TOOL_CALL", "TASK_SKILL"], identity: str) -> str:
    if kind not in {"TOOL_CALL", "TASK_SKILL"} or not isinstance(identity, str) or not identity.strip():
        raise ValueError("a known operation kind and persisted identity are required")
    # JSON escaping is injective, including separators, Unicode and NUL. The
    # canonical form can be stored in PG text without exposing it as a pathname.
    return json.dumps([kind, identity], ensure_ascii=True, separators=(",", ":"))


def _cumulative_scope(limits: RequestLimits, targets: tuple[TargetRule, ...]) -> FrozenScopeBudget:
    unsupported = (limits.max_inflight, limits.window_requests, limits.window_seconds)
    if any(value is not None for value in unsupported):
        raise ValueError("frozen window/inflight budgets are not supported by the shared scope protocol")
    if any(not rule.limits.is_unlimited for rule in targets):
        raise ValueError("frozen target budgets require authorized platform/route mapping")
    return FrozenScopeBudget(max_requests=limits.max_requests)


def freeze_egress_identity_budgets(policy: EffectiveEgressBudget, *,
                                  skill_keys: Sequence[str]) -> FrozenIdentityBudgets:
    """Compile a trusted effective snapshot; never silently drop a configured cap.

    Global window/concurrency rules belong to current public policy. The present
    frozen scope protocol handles cumulative totals. Target template conversion
    needs an authorized platform/route catalog and is explicitly rejected here.
    This function describes budgets, not Skill authorization.
    """
    if any(not isinstance(key, str) or not key.strip() for key in skill_keys):
        raise ValueError("frozen catalog must supply nonempty skill keys")
    execution = _cumulative_scope(policy.execution, policy.targets)
    _cumulative_scope(policy.skill_defaults.total, policy.skill_defaults.targets)
    invocations = {}
    for key in sorted(set(skill_keys) | policy.skills.keys()):
        skill = policy.for_skill(key)
        invocations[key] = _cumulative_scope(skill.total, skill.targets)
    return FrozenIdentityBudgets(execution=execution, invocations=invocations)
