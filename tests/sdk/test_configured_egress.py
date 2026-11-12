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
    settings = SharedSettings(_env_file=None)
    async with configured_host_egress(
        settings=settings,
        policy=resolve_egress_budget({}),
        skill_keys=(),
        session_factory=None,
        claim=forbidden,
        reader=None,
        minimum_stop_margin_seconds=0,
    ) as host:
        assert host is None


async def test_configured_budget_without_host_configuration_is_rejected():
    settings = SharedSettings(_env_file=None)
    with pytest.raises(MeteringUnavailable, match="storage"):
        async with configured_host_egress(
            settings=settings,
            policy=resolve_egress_budget({"execution": {"max_requests": 50}}),
            skill_keys=(),
            session_factory=None,
            claim=forbidden,
            reader=None,
            minimum_stop_margin_seconds=0,
        ):
            pytest.fail("configured budget silently bypassed")


async def test_unsupported_frozen_dimensions_do_not_become_unlimited():
    settings = SharedSettings(_env_file=None)
    with pytest.raises(MeteringUnavailable, match="configuration unsupported"):
        async with configured_host_egress(
            settings=settings,
            policy=resolve_egress_budget({"execution": {"max_inflight": 2}}),
            skill_keys=(),
            session_factory=None,
            claim=forbidden,
            reader=None,
            minimum_stop_margin_seconds=0,
        ):
            pytest.fail("unsupported budget silently bypassed")


async def test_console_empty_policy_never_constructs_limiter(monkeypatch):
    import muad_platform_sdk.configured_egress as module
    from muad_contracts.shared_egress import SharedEgressPolicy

    async def empty(*args):
        return SharedEgressPolicy()

    monkeypatch.setattr(module, "load_console_egress_policy", empty)
    monkeypatch.setattr(module, "create_redis_client", lambda *a, **k: pytest.fail("Redis constructed"))
    async with configured_host_egress(
        settings=SharedSettings(_env_file=None),
        policy=resolve_egress_budget({}),
        skill_keys=(),
        session_factory=None,
        claim=forbidden,
        reader=None,
        minimum_stop_margin_seconds=0,
        tenant_id="tenant",
    ) as host:
        assert host is None


async def test_settings_only_budget_assembly_needs_no_configuration_file(monkeypatch):
    from types import SimpleNamespace

    from muad_platform_sdk import configured_egress as module

    captured = {}

    class Redis:
        async def aclose(self):
            captured["closed"] = True

    async def create(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(module, "create_redis_client", lambda *a, **k: Redis())
    monkeypatch.setattr(module.HostEgressExecution, "create", create)

    async def provision(*args):
        return False

    monkeypatch.setattr(module, "provision_tenant_domain", provision)
    settings = SharedSettings(
        _env_file=None,
        egress_meter_domain="test-domain",
        egress_meter_epoch="test-epoch",
        egress_http_read_timeout_sec=7,
        egress_cleanup_sec=2,
        egress_stop_margin_sec=12,
    )
    async with configured_host_egress(
        settings=settings,
        policy=resolve_egress_budget({"execution": {"max_requests": 2}}),
        skill_keys=(),
        session_factory=lambda: "sessions",
        claim=lambda: SimpleNamespace(tenant_id="tenant"),
        reader=None,
        minimum_stop_margin_seconds=8,
    ):
        pass
    assert captured["settings"].domain == "test-domain"
    assert captured["settings"].epoch == "test-epoch"
    assert captured["settings"].lifetime.timeout_seconds == 7
    assert captured["closed"]


async def test_invalid_lifecycle_is_rejected_before_redis(monkeypatch):
    import muad_platform_sdk.configured_egress as module

    monkeypatch.setattr(module, "create_redis_client", lambda *a, **k: pytest.fail("Redis constructed"))
    settings = SharedSettings(_env_file=None, egress_http_read_timeout_sec=2, egress_renewal_interval_sec=3)
    with pytest.raises(MeteringUnavailable, match="lifecycle"):
        async with configured_host_egress(
            settings=settings,
            policy=resolve_egress_budget({"execution": {"max_requests": 2}}),
            skill_keys=(),
            session_factory=None,
            claim=forbidden,
            reader=None,
            minimum_stop_margin_seconds=0,
        ):
            pytest.fail("invalid renewal interval admitted")
