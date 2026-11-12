"""Frozen snapshot conversion and lazy backend activation; no storage boundary."""
import importlib

import pytest
from muad_contracts.egress_budget import resolve_egress_budget
from muad_contracts.shared_egress import FrozenIdentityBudgets, SharedEgressPolicy
from muad_platform_sdk.shared_metering import create_shared_metering


@pytest.mark.parametrize("budget", [
    {"execution": {"max_requests": 50}},
    {"invocations": {"skill": {"max_requests": 50}}},
])
def test_frozen_only_budget_does_not_skip_backend_when_public_rules_empty(budget):
    calls, backend = [], object()
    def factory(policy):
        calls.append(policy)
        return backend
    assert create_shared_metering(SharedEgressPolicy(), factory,
                                 budget=FrozenIdentityBudgets.model_validate(budget)) is backend
    assert len(calls) == 1


def freeze(value, skills=("a", "b")):
    function = importlib.import_module("muad_contracts.egress_identity").freeze_egress_identity_budgets
    return function(resolve_egress_budget(value), skill_keys=skills)


def test_snapshot_conversion_preserves_execution_defaults_and_skill_override():
    frozen = freeze({"execution": {"max_requests": 100}, "skill_defaults": {"total": {"max_requests": 50}},
                     "skills": {"b": {"total": {"max_requests": 20}}}})
    assert frozen.execution.max_requests == 100
    assert frozen.invocations["a"].max_requests == 50
    assert frozen.invocations["b"].max_requests == 20


def test_empty_snapshot_stays_unlimited_without_constructing_backend():
    frozen = freeze({})
    def forbidden(policy):
        pytest.fail("empty policy and snapshot must not construct backend")
    assert create_shared_metering(SharedEgressPolicy(), forbidden, budget=frozen) is None
    assert frozen.execution.max_requests is None
    assert all(b.max_requests is None for b in frozen.invocations.values())


@pytest.mark.parametrize("value", [
    {"execution": {"max_inflight": 1}},
    {"skill_defaults": {"total": {"window_requests": 50, "window_seconds": 60}}},
    {"skills": {"b": {"total": {"max_inflight": 1}}}},
    {"targets": [{"id": "assets", "match": {"platform_key": "p"}, "limits": {"max_requests": 50}}]},
])
def test_unsupported_frozen_dimensions_are_rejected_instead_of_dropped(value):
    with pytest.raises(ValueError):
        freeze(value)
