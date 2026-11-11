"""No-rules and configuration errors never become accidental local fallback."""
import pytest
from muad_common import SharedSettings
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk.configured_egress import configured_host_egress
from muad_platform_sdk.shared_redis import MeteringUnavailable


def forbidden():
    pytest.fail("disabled assembly must not capture source ownership")


async def test_unlimited_config_does_not_open_storage_or_require_source(monkeypatch):
    import muad_platform_sdk.configured_egress as module
    monkeypatch.setattr(module, "create_redis_client", lambda *a, **k: pytest.fail("Redis constructed"))
    settings = SharedSettings(_env_file=None, egress_control_file=None)
    async with configured_host_egress(settings=settings, policy=resolve_egress_budget({}), skill_keys=(),
                                     session_factory=None, claim=forbidden, reader=None,
                                     minimum_stop_margin_seconds=0) as host:
        assert host is None


async def test_configured_budget_without_host_configuration_is_rejected():
    settings = SharedSettings(_env_file=None, egress_control_file=None)
    with pytest.raises(MeteringUnavailable, match="configuration required"):
        async with configured_host_egress(
            settings=settings, policy=resolve_egress_budget({"execution": {"max_requests": 50}}),
            skill_keys=(), session_factory=None, claim=forbidden, reader=None, minimum_stop_margin_seconds=0,
        ):
            pytest.fail("configured budget silently bypassed")


async def test_unsupported_frozen_dimensions_do_not_become_unlimited():
    settings = SharedSettings(_env_file=None, egress_control_file=None)
    with pytest.raises(MeteringUnavailable, match="configuration unsupported"):
        async with configured_host_egress(
            settings=settings, policy=resolve_egress_budget({"execution": {"max_inflight": 2}}),
            skill_keys=(), session_factory=None, claim=forbidden, reader=None, minimum_stop_margin_seconds=0,
        ):
            pytest.fail("unsupported budget silently bypassed")
