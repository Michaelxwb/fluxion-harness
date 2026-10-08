"""B-07: startup configuration reaches the durable result dispatcher."""

import pytest
from muad_agent_worker.results.dispatcher import ResultDispatchPolicy
from muad_common import SharedSettings

pytestmark = pytest.mark.unit


def test_b07_dispatch_defaults_and_environment_reading(monkeypatch):
    policy = ResultDispatchPolicy.from_settings(SharedSettings(_env_file=None))
    assert policy.model_dump() == {
        "timeout_sec": 5,
        "lease_sec": 30,
        "poll_sec": 1,
        "batch_size": 32,
        "retry_base_sec": 1,
        "retry_cap_sec": 30,
        "max_attempts": 20,
    }
    monkeypatch.setenv("ASYNC_TOOL_DISPATCH_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("ASYNC_TOOL_DISPATCH_BATCH_SIZE", "3")
    configured = ResultDispatchPolicy.from_settings(SharedSettings(_env_file=None))
    assert configured.max_attempts == 7 and configured.batch_size == 3


@pytest.mark.parametrize("changes", [{"poll_sec": 0}, {"max_attempts": 0}, {"batch_size": 0}])
def test_b07_invalid_dispatch_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        ResultDispatchPolicy(**changes)
