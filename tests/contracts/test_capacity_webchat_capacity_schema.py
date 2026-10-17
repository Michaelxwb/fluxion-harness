"""B-201: pure capacity/model-pool settings validation and stable defaults."""

from dataclasses import asdict, replace

import pytest
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
    validate_platform_settings,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"capacity": {}}, {"model_pool": {}}],
)
def test_b201_missing_settings_use_declared_defaults(payload: dict[str, object] | None) -> None:
    settings = parse_platform_settings(payload)
    assert asdict(settings.capacity) == {
        "max_open_runs_per_tenant": 2000,
        "max_open_runs_per_user": 2,
        "queue_wait_timeout_sec": 60,
        "model_wait_timeout_sec": 30,
    }
    assert asdict(settings.model_pool) == {
        "dispatch_epoch_sec": 60,
        "input_estimate_multiplier": 1.25,
        "interactive_weight": 8,
        "background_weight": 1,
        "aging_interval_sec": 30,
    }


@pytest.mark.parametrize(
    "group,key,minimum,maximum",
    [
        ("capacity", "max_open_runs_per_tenant", 2, 100000),
        ("capacity", "max_open_runs_per_user", 2, 100),
        ("capacity", "queue_wait_timeout_sec", 1, 3600),
        ("capacity", "model_wait_timeout_sec", 1, 600),
        ("model_pool", "dispatch_epoch_sec", 10, 3600),
        ("model_pool", "interactive_weight", 1, 100),
        ("model_pool", "background_weight", 1, 100),
        ("model_pool", "aging_interval_sec", 1, 300),
    ],
)
def test_b201_integer_fields_enforce_inclusive_bounds(
    group: str, key: str, minimum: int, maximum: int,
) -> None:
    for value in (minimum, maximum):
        settings = parse_platform_settings({group: {key: value}})
        assert getattr(getattr(settings, group), key) == value
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(PlatformSettingsError, match=rf"{group}\.{key}"):
            parse_platform_settings({group: {key: value}})


@pytest.mark.parametrize(
    "group,key",
    [
        ("capacity", "max_open_runs_per_tenant"),
        ("capacity", "max_open_runs_per_user"),
        ("capacity", "queue_wait_timeout_sec"),
        ("capacity", "model_wait_timeout_sec"),
        ("model_pool", "dispatch_epoch_sec"),
        ("model_pool", "interactive_weight"),
        ("model_pool", "background_weight"),
        ("model_pool", "aging_interval_sec"),
    ],
)
@pytest.mark.parametrize("value", [True, False, 2.0, 2.5, "2", None, []])
def test_b201_integer_fields_reject_coercion(group: str, key: str, value: object) -> None:
    with pytest.raises(PlatformSettingsError, match=rf"{group}\.{key}"):
        parse_platform_settings({group: {key: value}})


@pytest.mark.parametrize("value", [1, 1.25, 4])
def test_b201_estimation_accepts_finite_numbers_within_bounds(value: int | float) -> None:
    settings = parse_platform_settings({"model_pool": {"input_estimate_multiplier": value}})
    assert settings.model_pool.input_estimate_multiplier == value


@pytest.mark.parametrize(
    "value",
    [0.9999, 4.0001, float("nan"), float("inf"), -float("inf"), 10**400, True, "1.25", None],
)
def test_b201_estimation_rejects_invalid_numbers(value: object) -> None:
    with pytest.raises(PlatformSettingsError, match=r"model_pool\.input_estimate_multiplier"):
        parse_platform_settings({"model_pool": {"input_estimate_multiplier": value}})


def test_b201_user_limit_must_fit_tenant_limit() -> None:
    with pytest.raises(PlatformSettingsError, match=r"capacity\.max_open_runs_per_user"):
        parse_platform_settings({"capacity": {"max_open_runs_per_tenant": 2, "max_open_runs_per_user": 3}})
    settings = parse_platform_settings({
        "capacity": {"max_open_runs_per_tenant": 2, "max_open_runs_per_user": 2},
    })
    assert settings.capacity.max_open_runs_per_user == settings.capacity.max_open_runs_per_tenant == 2


@pytest.mark.parametrize("group", ["capacity", "model_pool"])
@pytest.mark.parametrize("raw", [None, [], 2, True, "settings"])
def test_b201_groups_require_objects(group: str, raw: object) -> None:
    with pytest.raises(PlatformSettingsError, match=group):
        parse_platform_settings({group: raw})


@pytest.mark.parametrize("group", ["capacity", "model_pool"])
@pytest.mark.parametrize("key", ["unknown", "api_key", "token", "max_open_run"])
def test_b201_unknown_and_sensitive_fields_are_rejected(group: str, key: str) -> None:
    with pytest.raises(PlatformSettingsError, match=group):
        parse_platform_settings({group: {key: 1}})


def test_b201_unknown_root_groups_remain_rejected() -> None:
    with pytest.raises(PlatformSettingsError, match="model_pools"):
        parse_platform_settings({"model_pools": {}})


def test_b201_custom_settings_round_trip_without_changing_other_groups() -> None:
    defaults = default_platform_settings()
    settings = parse_platform_settings({
        "capacity": {"max_open_runs_per_tenant": 12, "max_open_runs_per_user": 4},
        "model_pool": {"background_weight": 2, "input_estimate_multiplier": 2},
    })
    assert settings.capacity.max_open_runs_per_user == 4
    assert settings.capacity.queue_wait_timeout_sec == defaults.capacity.queue_wait_timeout_sec
    assert settings.model_pool.background_weight == 2
    assert settings.model_pool.dispatch_epoch_sec == defaults.model_pool.dispatch_epoch_sec
    original, updated = asdict(defaults), asdict(settings)
    assert {k: v for k, v in updated.items() if k not in {"capacity", "model_pool"}} == {
        k: v for k, v in original.items() if k not in {"capacity", "model_pool"}
    }
    assert parse_platform_settings(updated) == settings


@pytest.mark.parametrize(
    "group,key,value",
    [
        ("capacity", "max_open_runs_per_tenant", True),
        ("capacity", "max_open_runs_per_user", 2.0),
        ("capacity", "queue_wait_timeout_sec", 0),
        ("capacity", "model_wait_timeout_sec", 601),
        ("model_pool", "dispatch_epoch_sec", False),
        ("model_pool", "interactive_weight", "8"),
        ("model_pool", "background_weight", 101),
        ("model_pool", "aging_interval_sec", 0),
        ("model_pool", "input_estimate_multiplier", float("nan")),
        ("model_pool", "input_estimate_multiplier", float("inf")),
        ("model_pool", "input_estimate_multiplier", True),
    ],
)
def test_b201_direct_typed_settings_cannot_bypass_validation(group: str, key: str, value: object) -> None:
    defaults = default_platform_settings()
    invalid = replace(defaults, **{group: replace(getattr(defaults, group), **{key: value})})
    assert isinstance(invalid, PlatformSettings)
    with pytest.raises(PlatformSettingsError, match=rf"{group}\.{key}"):
        validate_platform_settings(invalid)


def test_b201_direct_user_limit_must_fit_tenant_limit() -> None:
    defaults = default_platform_settings()
    invalid = replace(defaults, capacity=replace(
        defaults.capacity, max_open_runs_per_tenant=2, max_open_runs_per_user=3,
    ))
    with pytest.raises(PlatformSettingsError, match=r"capacity\.max_open_runs_per_user"):
        validate_platform_settings(invalid)
