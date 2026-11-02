"""B-305：容量验收 profile 的严格 typed schema 与规范示例。

真实边界：本文件只调用 `tests/capacity/profile.py` 的真实 typed 解析函数与真实 YAML 文件，
不联网、不连 PG/Redis、不使用任何替身。设计口径见
`agent-capacity-webchat.backend.design.md` §3.7「验收profile字段」与 §4.3。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.capacity.profile import (
    ProfileAcceptanceError,
    ProfileError,
    acceptance_blockers,
    file_digest,
    load_profile,
    parse_profile,
    profile_hash,
    profile_payload,
    referenced_env_vars,
    require_formal_acceptance,
)

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).resolve().parent / "profiles" / "agent-capacity-webchat.example.yml"
UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
_HARDWARE: dict[str, dict[str, int]] = {
    "console-platform": {
        "cpu_request_millicores": 1000,
        "cpu_limit_millicores": 2000,
        "memory_request_mb": 2048,
        "memory_limit_mb": 4096,
    },
    "agent-runtime": {
        "cpu_request_millicores": 1000,
        "cpu_limit_millicores": 2000,
        "memory_request_mb": 2048,
        "memory_limit_mb": 4096,
    },
    "agent-worker": {
        "cpu_request_millicores": 500,
        "cpu_limit_millicores": 2000,
        "memory_request_mb": 1024,
        "memory_limit_mb": 2048,
    },
    "im-gateway": {
        "cpu_request_millicores": 1000,
        "cpu_limit_millicores": 2000,
        "memory_request_mb": 1024,
        "memory_limit_mb": 2048,
    },
}
SECRET_KEY_WORDS = ("api_key", "apikey", "secret", "password", "bearer", "private_key")
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _document() -> dict[str, object]:
    """规范验收文档（与示例同构）；边界变异都从这份完整输入出发。"""
    return {
        "workload": {
            "users": 1000,
            "active_runs_per_user": 1,
            "ramp_sec": 60,
            "steady_sec": 600,
            "turns_per_user": 8,
            "input_distribution": {
                "think_time_ms": {"p50": 3000, "p95": 15000, "max": 60000},
                "text_bytes": {"p50": 256, "p95": 4096, "max": 65536},
            },
            "attachment_distribution": {
                "ratio": 0.1,
                "upload_bytes": {"p50": 1048576, "p95": 10485760, "max": 52428800},
            },
        },
        "model": {
            "mode": "CONTROLLED_PROBE",
            "probe_latency_ms": {"p50": 800, "p95": 2500, "max": 10000},
            "output_distribution": {"p50": 256, "p95": 1024, "max": 4096},
        },
        "slo": {
            "admission_p95_ms": 500,
            "admission_p99_ms": 1500,
            "framework_first_delta_p95_ms": 1200,
            "recovery_max_sec": 60,
            "max_system_error_ratio": 0.01,
        },
        "resources": {
            "hardware": {unit: dict(spec) for unit, spec in _HARDWARE.items()},
            "process_counts": {**{unit: 1 for unit in UNITS}, "console-platform": 4},
            "replica_counts": {
                "console-platform": 1,
                "agent-runtime": 8,
                "agent-worker": 4,
                "im-gateway": 2,
            },
            "max_replica_counts": {
                "console-platform": 1,
                "agent-runtime": 8,
                "agent-worker": 4,
                "im-gateway": 2,
            },
            "pg_connection_budget": 300,
            "fd_budget": 16384,
            "storage_budget": {
                "event_bytes_per_day": 5368709120,
                "retention_days": 30,
                "artifact_bytes": 107374182400,
            },
        },
    }


def _real_document() -> dict[str, object]:
    """REAL 模式文档：只引用 env 变量名，不内嵌任何密钥原文。"""
    document = _document()
    document["model"] = {
        "mode": "REAL",
        "credentials_and_quotas": [
            {
                "provider": "openai-compatible",
                "model": "gpt-4o-mini",
                "credential_ref": "env:MUAD_CAPACITY_MODEL_KEY",
                "quota_group_ref": "env:MUAD_CAPACITY_QUOTA_GROUP",
                "max_inflight": 8,
                "rpm": 600,
                "tpm": 200000,
            },
        ],
    }
    return document


def _resolve(node: Any, part: str) -> Any:
    return node[int(part)] if isinstance(node, list) else node[part]


def _set_path(document: dict[str, Any], path: str, value: object) -> None:
    parts = path.split(".")
    node: Any = document
    for part in parts[:-1]:
        node = _resolve(node, part)
    node[parts[-1]] = value


def _drop_path(document: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    node: Any = document
    for part in parts[:-1]:
        node = _resolve(node, part)
    del node[parts[-1]]


def test_b305_example_profile_parses_and_is_acceptance_ready() -> None:
    profile = load_profile(EXAMPLE)
    assert profile.workload.users == 1000  # 1000 用户目标只能由真实执行负载声明
    assert profile.workload.turns_per_user >= 1
    assert profile.workload.input_distribution.text_bytes.max >= 1
    assert profile.model.mode == "CONTROLLED_PROBE"
    assert profile.model.probe_latency_ms is not None
    assert profile.model.output_distribution is not None
    assert profile.slo is not None and profile.resources is not None
    assert acceptance_blockers(profile) == ()
    require_formal_acceptance(profile)


def test_b305_example_matches_canonical_document() -> None:
    assert profile_payload(load_profile(EXAMPLE)) == profile_payload(parse_profile(_document()))


def test_b305_no_embedded_secrets_and_no_env_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = EXAMPLE.read_text(encoding="utf-8")
    lowered = source.lower()
    assert [word for word in SECRET_KEY_WORDS if word in lowered] == []
    assert [pattern.pattern for pattern in SECRET_VALUE_PATTERNS if pattern.search(source)] == []
    assert referenced_env_vars(load_profile(EXAMPLE)) == ()

    for name in referenced_env_vars(parse_profile(_real_document())):
        monkeypatch.delenv(name, raising=False)
    assert parse_profile(_real_document()) == parse_profile(_real_document())  # 解析不读环境


@pytest.mark.parametrize(
    "path,value",
    [
        ("workload.users", True),
        ("workload.users", 1000.5),
        ("workload.users", "1000"),
        ("workload.turns_per_user", True),
        ("workload.turns_per_user", 0),
        ("workload.active_runs_per_user", 0),
        ("workload.active_runs_per_user", 1001),
        ("workload.ramp_sec", -1),
        ("workload.steady_sec", 0),
        ("workload.input_distribution.text_bytes.p50", 1.5),
        ("workload.attachment_distribution.upload_bytes.max", 0),
        ("resources.pg_connection_budget", True),
        ("resources.pg_connection_budget", 0),
        ("resources.fd_budget", 4096.5),
        ("resources.process_counts.agent-runtime", False),
        ("resources.max_replica_counts.agent-worker", 0),
        ("resources.storage_budget.retention_days", True),
        ("resources.storage_budget.retention_days", 0),
        ("resources.storage_budget.artifact_bytes", -1),
    ],
)
def test_b305_integer_fields_reject_bool_float_and_out_of_range(path: str, value: object) -> None:
    document = _document()
    _set_path(document, path, value)
    with pytest.raises(ProfileError, match=re.escape(path)):
        parse_profile(document)


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), float("inf"), float("-inf"), "500", None, 0, -1],
)
def test_b305_slo_numbers_must_be_finite_positive(value: object) -> None:
    document = _document()
    _set_path(document, "slo.admission_p95_ms", value)
    with pytest.raises(ProfileError, match="slo.admission_p95_ms"):
        parse_profile(document)


@pytest.mark.parametrize("value", [1.5, -0.1, float("nan"), True, "0.01"])
def test_b305_error_ratio_is_bounded_number(value: object) -> None:
    document = _document()
    _set_path(document, "slo.max_system_error_ratio", value)
    with pytest.raises(ProfileError, match="slo.max_system_error_ratio"):
        parse_profile(document)


def test_b305_slo_percentiles_must_be_ordered() -> None:
    document = _document()
    _set_path(document, "slo.admission_p95_ms", 2000)
    with pytest.raises(ProfileError, match="slo.admission_p95_ms"):
        parse_profile(document)


def test_b305_distribution_percentiles_must_be_ordered() -> None:
    for path, value, fragment in (
        ("workload.input_distribution.text_bytes.p50", 5000, "p95"),
        ("workload.input_distribution.text_bytes.p95", 70000, "max"),
        ("workload.input_distribution.think_time_ms.max", -1, "max"),
    ):
        document = _document()
        _set_path(document, path, value)
        with pytest.raises(ProfileError, match=fragment):
            parse_profile(document)


def test_b305_users_below_target_only_blocks_the_pass() -> None:
    document = _document()
    _set_path(document, "workload.users", 999)
    profile = parse_profile(document)  # 诊断结果仍可生成
    blockers = acceptance_blockers(profile)
    assert any("workload.users" in blocker for blocker in blockers)
    with pytest.raises(ProfileAcceptanceError, match="workload.users"):
        require_formal_acceptance(profile)


@pytest.mark.parametrize(
    "path",
    [
        "workload",
        "model",
        "workload.users",
        "workload.turns_per_user",
        "workload.input_distribution",
        "workload.attachment_distribution",
        "workload.input_distribution.text_bytes",
        "workload.attachment_distribution.ratio",
        "model.mode",
        "model.probe_latency_ms",
        "model.output_distribution",
        "slo.admission_p99_ms",
        "slo.framework_first_delta_p95_ms",
        "slo.recovery_max_sec",
        "slo.max_system_error_ratio",
        "resources.hardware",
        "resources.pg_connection_budget",
        "resources.fd_budget",
        "resources.storage_budget.event_bytes_per_day",
        "resources.max_replica_counts.im-gateway",
    ],
)
def test_b305_missing_required_keys_are_rejected(path: str) -> None:
    document = _document()
    _drop_path(document, path)
    with pytest.raises(ProfileError):
        parse_profile(document)


def test_b305_missing_slo_and_resources_never_yield_an_acceptance_pass() -> None:
    for section in ("slo", "resources"):
        document = _document()
        del document[section]
        profile = parse_profile(document)
        blockers = acceptance_blockers(profile)
        assert any(section in blocker for blocker in blockers)
        with pytest.raises(ProfileAcceptanceError, match=section):
            require_formal_acceptance(profile)


@pytest.mark.parametrize(
    "path",
    [
        "profile_extra",
        "workload.unknown",
        "workload.input_distribution.unknown",
        "workload.attachment_distribution.unknown",
        "model.unknown",
        "slo.unknown",
        "resources.unknown",
        "resources.hardware.console-platform.unknown",
        "resources.storage_budget.unknown",
        "resources.max_replica_counts.unknown-unit",
    ],
)
def test_b305_unknown_keys_are_rejected(path: str) -> None:
    document = _document()
    parts = path.split(".")
    node: Any = document
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = 1
    with pytest.raises(ProfileError):
        parse_profile(document)


@pytest.mark.parametrize("mode", ["MOCK", "controlled_probe", "", 1, None])
def test_b305_model_mode_is_a_closed_enum(mode: object) -> None:
    document = _document()
    _set_path(document, "model.mode", mode)
    with pytest.raises(ProfileError, match="model.mode"):
        parse_profile(document)


def test_b305_real_mode_requires_quota_evidence() -> None:
    document = _real_document()
    del document["model"]["credentials_and_quotas"]  # type: ignore[index]
    with pytest.raises(ProfileError, match="credentials_and_quotas"):
        parse_profile(document)


def test_b305_mode_specific_keys_are_rejected() -> None:
    for key, value in (
        ("probe_latency_ms", {"p50": 1, "p95": 2, "max": 3}),
        ("output_distribution", {"p50": 1, "p95": 2, "max": 3}),
    ):
        document = _real_document()
        document["model"][key] = value  # type: ignore[index]
        with pytest.raises(ProfileError, match=key):
            parse_profile(document)

    document = _document()
    document["model"]["credentials_and_quotas"] = [{"provider": "x"}]  # type: ignore[index]
    with pytest.raises(ProfileError, match="credentials_and_quotas"):
        parse_profile(document)


def test_b305_real_mode_records_quota_evidence_with_env_references_only() -> None:
    profile = parse_profile(_real_document())
    assert profile.model.mode == "REAL"
    entries = profile.model.credentials_and_quotas
    assert len(entries) == 1
    assert entries[0].credential_ref == "env:MUAD_CAPACITY_MODEL_KEY"
    assert entries[0].max_inflight == 8 and entries[0].rpm == 600 and entries[0].tpm == 200000
    assert referenced_env_vars(profile) == (
        "MUAD_CAPACITY_MODEL_KEY",
        "MUAD_CAPACITY_QUOTA_GROUP",
    )
    require_formal_acceptance(profile)


@pytest.mark.parametrize(
    "path,value",
    [
        ("max_inflight", True),
        ("max_inflight", 0),
        ("max_inflight", None),
        ("rpm", 0),
        ("tpm", 1.5),
        ("credential_ref", "sk-live-abcdefghijklmnop"),
        ("credential_ref", "plain-ref"),
        ("quota_group_ref", "MUAD_CAPACITY_QUOTA_GROUP"),
        ("provider", ""),
    ],
)
def test_b305_quota_entries_are_strictly_typed(path: str, value: object) -> None:
    document = _real_document()
    _set_path(document, f"model.credentials_and_quotas.0.{path}", value)
    with pytest.raises(ProfileError):
        parse_profile(document)


def test_b305_quota_entries_must_declare_a_quota_dimension() -> None:
    document = _real_document()
    entry: dict[str, object] = document["model"]["credentials_and_quotas"][0]  # type: ignore[index]
    entry["rpm"] = None
    entry["tpm"] = None
    with pytest.raises(ProfileError, match="rpm"):
        parse_profile(document)


@pytest.mark.parametrize(
    "path,value",
    [
        ("api_key", "sk-live-abcdefghijklmnop"),
        ("workload.text_token", "abc"),
        ("workload.input_distribution.think_time_ms.password", "hunter2"),
        ("slo.bearer", "abc"),
    ],
)
def test_b305_credential_fields_are_rejected_without_echoing_the_secret(path: str, value: object) -> None:
    document = _document()
    parts = path.split(".")
    node: Any = document
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    with pytest.raises(ProfileError) as caught:
        parse_profile(document)
    assert value not in str(caught.value)


def test_b305_secret_looking_values_are_rejected_without_echoing_them() -> None:
    document = _document()
    _set_path(document, "model.provider", "sk-live-abcdefghijklmnop")
    with pytest.raises(ProfileError) as caught:
        parse_profile(document)
    assert "sk-live-abcdefghijklmnop" not in str(caught.value)


def test_b305_resources_keep_console_single_pod_and_allow_gateway_replicas() -> None:
    """设计 §4.1：Console 仍是单 Pod 容量口径；Gateway 允许 N 副本（清单基线 2）。"""
    document = _document()
    _set_path(document, "resources.max_replica_counts.console-platform", 2)
    with pytest.raises(ProfileError, match="console-platform"):
        parse_profile(document)

    document = _document()
    _set_path(document, "resources.max_replica_counts.im-gateway", 3)
    profile = parse_profile(document)
    assert profile.resources is not None
    assert profile.resources.max_replica_counts["im-gateway"] == 3


def test_b305_resources_require_every_deployment_unit_and_replica_room() -> None:
    document = _document()
    del document["resources"]["hardware"]["agent-worker"]  # type: ignore[index]
    with pytest.raises(ProfileError, match="agent-worker"):
        parse_profile(document)

    document = _document()
    _set_path(document, "resources.replica_counts.agent-runtime", 9)
    with pytest.raises(ProfileError, match="resources.replica_counts.agent-runtime"):
        parse_profile(document)


def test_b305_hardware_requests_must_fit_limits() -> None:
    document = _document()
    _set_path(document, "resources.hardware.agent-runtime.memory_request_mb", 16384)
    with pytest.raises(ProfileError, match="memory_request_mb"):
        parse_profile(document)


def test_b305_attachment_ratio_governs_upload_distribution() -> None:
    document = _document()
    _set_path(document, "workload.attachment_distribution.ratio", 1.5)
    with pytest.raises(ProfileError, match="ratio"):
        parse_profile(document)

    document = _document()
    _set_path(document, "workload.attachment_distribution.ratio", True)
    with pytest.raises(ProfileError, match="ratio"):
        parse_profile(document)

    document = _document()
    _set_path(document, "workload.attachment_distribution.ratio", 0.0)
    _drop_path(document, "workload.attachment_distribution.upload_bytes")
    profile = parse_profile(document)  # 纯文本负载合法
    assert profile.workload.attachment_distribution.upload_bytes is None

    document = _document()
    _set_path(document, "workload.attachment_distribution.ratio", 0.5)
    _drop_path(document, "workload.attachment_distribution.upload_bytes")
    with pytest.raises(ProfileError, match="upload_bytes"):
        parse_profile(document)


def test_b305_profile_hash_freezes_content_and_round_trips() -> None:
    profile = parse_profile(_document())
    payload = profile_payload(profile)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert profile_hash(profile) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert profile_hash(parse_profile(payload)) == profile_hash(profile)

    changed = _document()
    _set_path(changed, "workload.steady_sec", 601)
    assert profile_hash(parse_profile(changed)) != profile_hash(profile)

    assert file_digest(EXAMPLE) == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    assert file_digest(EXAMPLE) != profile_hash(profile)


def test_b305_load_profile_reports_missing_broken_or_non_mapping_input(tmp_path: Path) -> None:
    with pytest.raises(ProfileError):
        load_profile(tmp_path / "missing.yml")

    broken = tmp_path / "broken.yml"
    broken.write_text("workload: [unclosed\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(broken)

    sequence = tmp_path / "sequence.yml"
    sequence.write_text("- not\n- an\n- object\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(sequence)
