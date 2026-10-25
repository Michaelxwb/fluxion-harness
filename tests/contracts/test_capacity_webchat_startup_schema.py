"""B-203: real startup settings, strict environment inputs and resource invariants."""

from pathlib import Path

import pytest
from muad_common import SharedSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit

DEFAULTS: dict[str, int | float] = {
    "runtime_execution_concurrency": 8, "worker_execution_concurrency": 4,
    "gateway_max_concurrent_handlers": 8, "gateway_max_pending_handlers": 256,
    "gateway_max_pending_per_route": 8, "gateway_max_pending_commands": 32,
    "gateway_max_sse_subscribers": 1100, "runtime_max_sse_subscribers": 1100,
    "db_pool_size": 10, "db_max_overflow": 0, "db_pool_timeout_sec": 5.0,
    "redis_max_connections": 50,
    "control_http_max_connections": 32, "control_http_max_keepalive": 16,
    "stream_http_max_connections": 1100, "stream_http_max_keepalive": 64,
    "model_http_max_connections": 64, "model_http_max_keepalive": 32,
    "mcp_http_max_connections": 16, "mcp_http_max_keepalive": 8, "mcp_max_inflight": 8,
    "egress_http_max_connections": 1024, "egress_http_max_keepalive": 256, "egress_max_inflight": 8,
    "upload_http_max_connections": 8, "upload_http_max_keepalive": 4,
    "http_connect_timeout_sec": 5.0, "http_pool_timeout_sec": 2.0,
    "control_http_read_timeout_sec": 10.0, "stream_http_read_timeout_sec": 45.0,
    "model_http_read_timeout_sec": 60.0, "upload_http_read_timeout_sec": 60.0,
    "mcp_http_read_timeout_sec": 30.0, "egress_http_read_timeout_sec": 30.0,
    "run_lease_sec": 60, "run_heartbeat_sec": 20, "run_reaper_interval_sec": 30,
    "task_lease_sec": 60, "task_heartbeat_sec": 20,
    "model_call_lease_sec": 60, "model_call_heartbeat_sec": 20,
    "run_event_heartbeat_sec": 15, "run_event_fallback_poll_sec": 5.0,
    "stream_delta_flush_interval_ms": 300, "stream_delta_flush_bytes": 8192,
    "sse_event_max_bytes": 262144, "sse_subscriber_buffer_bytes": 1048576,
    "sse_total_buffer_bytes": 67108864,
    "chat_auth_recheck_sec": 15.0, "chat_exchange_max_body_bytes": 4096,
    "chat_exchange_per_ip_per_minute": 6000,
    "chat_message_hard_max_bytes": 1048576, "chat_upload_hard_max_bytes": 52428800,
    "channel_delivery_concurrency": 4, "channel_delivery_timeout_sec": 5.0,
    "channel_delivery_lease_sec": 30.0, "channel_delivery_poll_sec": 1.0,
    "channel_delivery_batch_size": 32, "channel_delivery_retry_base_sec": 1.0,
    "channel_delivery_retry_cap_sec": 30.0, "channel_delivery_max_attempts": 20,
    "task_cancel_check_sec": 2, "worker_poll_interval_sec": 5,
    "batch_platform_limit": 16, "scheduler_poll_interval_sec": 10, "scheduler_batch_size": 100,
    "task_deadline_sweep_interval_sec": 30, "delivery_poll_interval_sec": 5, "delivery_batch_size": 20,
    "async_tool_dispatch_timeout_sec": 5.0, "async_tool_dispatch_lease_sec": 30.0,
    "async_tool_dispatch_poll_sec": 1.0, "async_tool_dispatch_batch_size": 32,
    "async_tool_dispatch_retry_base_sec": 1.0, "async_tool_dispatch_retry_cap_sec": 30.0,
    "async_tool_dispatch_max_attempts": 20, "im_progress_updates_per_second": 10.0,
}
INTEGER_FIELDS = [key for key, value in DEFAULTS.items() if type(value) is int]
FLOAT_FIELDS = [key for key, value in DEFAULTS.items() if type(value) is float]
HTTP_POOLS = ["control", "stream", "model", "mcp", "egress", "upload"]


@pytest.fixture(autouse=True)
def clean_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in set(SharedSettings.model_fields).union(DEFAULTS):
        monkeypatch.delenv(name.upper(), raising=False)


def test_b203_documented_resource_defaults() -> None:
    settings = SharedSettings(_env_file=None)
    assert DEFAULTS.keys() <= SharedSettings.model_fields.keys()
    assert {name: getattr(settings, name) for name in DEFAULTS} == DEFAULTS


@pytest.mark.parametrize("name", list(DEFAULTS))
def test_b203_every_resource_setting_can_be_read_from_environment(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = DEFAULTS[name]
    value = default * 2 if type(default) is float else max(1, default - 1)
    if name.endswith("_lease_sec") and type(default) is int:
        value = default + 3
    assert value != default
    monkeypatch.setenv(name.upper(), str(value))
    assert getattr(SharedSettings(_env_file=None), name) == value


@pytest.mark.parametrize("name", INTEGER_FIELDS)
@pytest.mark.parametrize("value", [True, False, 8.0, 8.5, "8.0", "8e1"])
def test_b203_integer_settings_do_not_coerce_bool_or_fraction(name: str, value: object) -> None:
    with pytest.raises(ValidationError, match=name):
        SharedSettings(_env_file=None, **{name: value})


@pytest.mark.parametrize("name", list(DEFAULTS))
def test_b203_malformed_environment_values_fail_at_startup(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(name.upper(), "not-a-number")
    with pytest.raises(ValidationError, match=name):
        SharedSettings(_env_file=None)


@pytest.mark.parametrize("name", FLOAT_FIELDS)
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "-inf", "NaN"])
def test_b203_float_settings_require_finite_numbers(name: str, value: object) -> None:
    with pytest.raises(ValidationError, match=name):
        SharedSettings(_env_file=None, **{name: value})


@pytest.mark.parametrize("name", list(DEFAULTS))
def test_b203_negative_resource_limits_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match=name):
        SharedSettings(_env_file=None, **{name: -1})


@pytest.mark.parametrize("name", list(DEFAULTS))
def test_b203_zero_has_only_declared_connection_semantics(name: str) -> None:
    if name == "db_max_overflow" or name.endswith("_max_keepalive"):
        assert getattr(SharedSettings(_env_file=None, **{name: 0}), name) == 0
    else:
        with pytest.raises(ValidationError, match=name):
            SharedSettings(_env_file=None, **{name: 0})


@pytest.mark.parametrize("prefix", HTTP_POOLS)
def test_b203_keepalive_must_fit_connection_budget(prefix: str) -> None:
    connections, keepalive = f"{prefix}_http_max_connections", f"{prefix}_http_max_keepalive"
    for value in (0, 8):
        settings = SharedSettings(_env_file=None, **{connections: 8, keepalive: value})
        assert getattr(settings, keepalive) == value
    with pytest.raises(ValidationError, match=keepalive):
        SharedSettings(_env_file=None, **{connections: 8, keepalive: 9})


@pytest.mark.parametrize("prefix", ["run", "task", "model_call"])
def test_b203_leases_cover_three_heartbeats(prefix: str) -> None:
    lease, heartbeat = f"{prefix}_lease_sec", f"{prefix}_heartbeat_sec"
    accepted = SharedSettings(_env_file=None, **{lease: 3, heartbeat: 1})
    assert getattr(accepted, lease) == 3
    with pytest.raises(ValidationError, match=lease):
        SharedSettings(_env_file=None, **{lease: 2, heartbeat: 1})


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"runtime_execution_concurrency": 0}, "runtime_execution_concurrency"),
        ({"runtime_execution_concurrency": 257}, "runtime_execution_concurrency"),
        ({"worker_execution_concurrency": 65}, "worker_execution_concurrency"),
        ({"gateway_max_sse_subscribers": 100001}, "gateway_max_sse_subscribers"),
        ({"runtime_max_sse_subscribers": 0}, "runtime_max_sse_subscribers"),
        ({"db_pool_size": 0}, "db_pool_size"), ({"db_max_overflow": -1}, "db_max_overflow"),
        ({"stream_delta_flush_interval_ms": 9}, "stream_delta_flush_interval_ms"),
        ({"stream_delta_flush_interval_ms": 501}, "stream_delta_flush_interval_ms"),
        ({"stream_delta_flush_bytes": 1023}, "stream_delta_flush_bytes"),
        ({"stream_delta_flush_bytes": 65537}, "stream_delta_flush_bytes"),
        ({"chat_auth_recheck_sec": 61}, "chat_auth_recheck_sec"),
        ({"chat_exchange_per_ip_per_minute": 0}, "chat_exchange_per_ip_per_minute"),
        ({"stream_http_read_timeout_sec": 44}, "stream_http_read_timeout_sec"),
        ({"sse_event_max_bytes": 524289}, "sse_subscriber_buffer_bytes"),
        ({"sse_total_buffer_bytes": 1048575}, "sse_total_buffer_bytes"),
        ({"channel_delivery_timeout_sec": 30}, "channel_delivery_timeout_sec"),
        ({"channel_delivery_retry_base_sec": 31}, "channel_delivery_retry_base_sec"),
        ({"async_tool_dispatch_timeout_sec": 30}, "async_tool_dispatch_timeout_sec"),
        ({"async_tool_dispatch_retry_base_sec": 31}, "async_tool_dispatch_retry_base_sec"),
        ({"runtime_execution_concurrency": 256}, "model_http_max_connections"),
    ],
)
def test_b203_invalid_ranges_and_cross_field_combinations(changes: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        SharedSettings(_env_file=None, **changes)


def test_b203_resource_boundaries_and_fractional_timeouts_are_supported() -> None:
    settings = SharedSettings(
        _env_file=None, runtime_execution_concurrency=256, worker_execution_concurrency=64,
        model_http_max_connections=256,
        gateway_max_sse_subscribers=100000, runtime_max_sse_subscribers=100000,
        db_max_overflow=0, http_connect_timeout_sec=0.5, chat_auth_recheck_sec=60,
        stream_delta_flush_interval_ms=10, stream_delta_flush_bytes=1024,
        sse_event_max_bytes=524288, sse_subscriber_buffer_bytes=1048576,
        sse_total_buffer_bytes=1048576,
    )
    assert settings.http_connect_timeout_sec == 0.5
    assert settings.sse_subscriber_buffer_bytes == 2 * settings.sse_event_max_bytes


def test_b203_invalid_startup_errors_do_not_print_credentials() -> None:
    secret = "startup-private-canary"
    with pytest.raises(ValidationError) as failure:
        SharedSettings(_env_file=None, internal_service_token=secret, database_url=secret,
                       run_lease_sec=2, run_heartbeat_sec=1)
    assert secret not in str(failure.value)
    settings = SharedSettings(_env_file=None, internal_service_token=secret, database_url=secret)
    assert secret not in repr(settings)


def test_b203_startup_resources_do_not_become_business_or_model_credentials() -> None:
    settings = SharedSettings(_env_file=None)
    assert settings.internal_service_token is None
    assert settings.database_url is None
    assert settings.redis_url is None
    forbidden = {"model_api_key", "model_pool", "capacity", "webchat", "anonymous"}
    assert not forbidden & set(SharedSettings.model_fields)
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    keys = [line.split("=", 1)[0] for line in example.splitlines() if line and not line.startswith("#")]
    assert len(keys) == len(set(keys))
    assert {name.upper() for name in DEFAULTS} <= set(keys)
