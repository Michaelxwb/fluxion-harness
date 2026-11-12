import pytest


def test_b_06_empty_policy_is_disabled_without_creating_metering_state():
    from muad_contracts.shared_egress import SharedEgressPolicy

    policy = SharedEgressPolicy.from_runtime_config({})
    assert policy.enabled is False
    assert policy.rules == ()


def test_b_06_invalid_shared_rule_is_rejected():
    from muad_contracts.shared_egress import SharedEgressPolicy

    with pytest.raises(ValueError, match="greater than 0"):
        SharedEgressPolicy.from_runtime_config(
            {"rules": [{"id": "assets", "max_requests": 0}]}
        )


def test_b_06_stable_identity_does_not_follow_segment():
    from muad_contracts.shared_egress import TrustedInvocation

    first = TrustedInvocation(
        execution_id="run-1", invocation_id="call-1", segment_id="pod-a"
    )
    second = first.with_segment("pod-b")
    assert second.execution_id == first.execution_id
    assert second.invocation_id == first.invocation_id
    assert second.segment_id == "pod-b"


@pytest.mark.parametrize("fields", [
    {"max_requests": True}, {"max_requests": "50"},
    {"window_requests": 1}, {"window_seconds": 60.0},
    {"window_requests": 1, "window_seconds": float("inf")},
    {"max_inflight": 0}, {"unknown": 1}, {},
])
def test_b_06_invalid_limits_do_not_turn_into_unlimited(fields):
    from muad_contracts.shared_egress import SharedRule

    with pytest.raises(ValueError):
        SharedRule.model_validate({"id": "assets", "target_instance_id": "p", "route_id": "assets", **fields})


def test_b_06_duplicate_rules_and_unknown_policy_fields_fail():
    from muad_contracts.shared_egress import SharedEgressPolicy

    rule = {"id": "assets", "target_instance_id": "p", "route_id": "assets", "max_requests": 50}
    for raw in ({"rules": [rule, rule]}, {"typo": True}, None):
        with pytest.raises(ValueError):
            SharedEgressPolicy.from_runtime_config(raw)


def test_b_06_unknown_targets_and_unsupported_clients_are_explicit():
    from muad_contracts.shared_egress import SharedEgressPolicy

    policy = SharedEgressPolicy.from_runtime_config({"rules": [{
        "id": "assets", "target_instance_id": "p", "route_id": "GET assets", "max_requests": 50,
    }]})
    assert policy.select("p", "GET assets", supported=True).rule_ids == ("assets",)
    assert policy.select("other", "GET assets", supported=True).rule_ids == ()
    assert policy.select("p", "POST assets", supported=True).rule_ids == ()
    unsupported = policy.select("p", "GET assets", supported=False)
    assert unsupported.coverage == "UNSUPPORTED"
    assert unsupported.rule_ids == ()


def test_b_06_disabled_policy_never_constructs_metering_backend():
    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.shared_metering import create_shared_metering

    calls = []
    def factory(policy):
        calls.append(policy)
        raise AssertionError("no metering backend may be constructed")
    assert create_shared_metering(SharedEgressPolicy.from_runtime_config({}), factory) is None
    assert calls == []
