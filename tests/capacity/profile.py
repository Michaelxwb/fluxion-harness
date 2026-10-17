"""容量验收 profile 的严格 typed schema（设计 §3.7「验收profile字段」、§4.3 分层验收）。

本模块只做纯解析与校验：不读环境变量、不联网、不连 PG/Redis，也不解析任何密钥。
`REAL` 模式的模型额度证据只允许 `env:NAME` 引用；`parse_profile` 允许省略 `slo`/`resources`
（仍可生成诊断结果），正式验收由 `require_formal_acceptance` 裁决：缺任一必填项明确拒绝，
不给容量验收 pass（E-21）。所有校验错误消息都不回带原始密钥/内部内容。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

__all__ = [
    "AttachmentDistribution",
    "CapacityProfile",
    "HardwareSpec",
    "InputDistribution",
    "ModelProfile",
    "PercentileDistribution",
    "ProfileAcceptanceError",
    "ProfileError",
    "QuotaEntry",
    "ResourceProfile",
    "SloProfile",
    "StorageBudget",
    "WorkloadProfile",
    "acceptance_blockers",
    "file_digest",
    "is_acceptance_ready",
    "load_profile",
    "parse_profile",
    "profile_hash",
    "profile_payload",
    "referenced_env_vars",
    "require_formal_acceptance",
]

_UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
_SINGLE_POD_UNITS = ("console-platform", "im-gateway")
_TOP_LEVEL_KEYS = ("workload", "model", "slo", "resources")
_SLO_KEYS = (
    "admission_p95_ms",
    "admission_p99_ms",
    "framework_first_delta_p95_ms",
    "recovery_max_sec",
    "max_system_error_ratio",
)
_MIN_ACCEPTANCE_USERS = 1000
_ENV_REFERENCE = re.compile(r"env:[A-Z][A-Z0-9_]{0,63}")
_SECRET_KEY_WORDS = frozenset(
    {
        "secret",
        "password",
        "passwd",
        "token",
        "apikey",
        "authorization",
        "cookie",
        "bearer",
        "privatekey",
        "clientsecret",
    }
)
_SECRET_KEY_NAMES = frozenset(
    {"api_key", "api-key", "access_key", "secret_key", "private_key", "client_secret"}
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
)


class ProfileError(ValueError):
    """容量验收 profile 的解析/校验失败；消息不回带密钥或原始内部内容。"""


class ProfileAcceptanceError(ProfileError):
    """正式验收必填项缺失：可以生成诊断结果，但不能给出容量验收 pass。"""


@dataclass(frozen=True, slots=True)
class PercentileDistribution:
    """有序的 p50/p95/max 分布，单位由所在字段决定（毫秒或 UTF-8 字节）。"""

    p50: int
    p95: int
    max: int


@dataclass(frozen=True, slots=True)
class InputDistribution:
    """消息频率（think_time_ms）与正文大小（text_bytes）；禁止用空闲连接冒充执行负载。"""

    think_time_ms: PercentileDistribution
    text_bytes: PercentileDistribution


@dataclass(frozen=True, slots=True)
class AttachmentDistribution:
    """附件轮次比例；ratio>0 必须给出上传字节分布，ratio=0 表示纯文本负载。"""

    ratio: float
    upload_bytes: PercentileDistribution | None


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    """1000 用户目标对应的负载形状；users≥1000 才可验收本目标。"""

    users: int
    active_runs_per_user: int
    ramp_sec: int
    steady_sec: int
    turns_per_user: int
    input_distribution: InputDistribution
    attachment_distribution: AttachmentDistribution


@dataclass(frozen=True, slots=True)
class QuotaEntry:
    """真实模型/Key 的额度证据；凭据只以 env: 引用出现，绝不内嵌密钥原文。"""

    provider: str
    model: str
    credential_ref: str
    quota_group_ref: str | None
    max_inflight: int
    rpm: int | None
    tpm: int | None


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """模型模式：CONTROLLED_PROBE（真实 HTTP 探针）或 REAL（真实供方额度证据）。"""

    mode: str
    probe_latency_ms: PercentileDistribution | None
    output_distribution: PercentileDistribution | None
    credentials_and_quotas: tuple[QuotaEntry, ...]


@dataclass(frozen=True, slots=True)
class SloProfile:
    """正式验收阈值，无默认值：运行前确定并在报告中记录（§3.7 / §4.3）。"""

    admission_p95_ms: float
    admission_p99_ms: float
    framework_first_delta_p95_ms: float
    recovery_max_sec: float
    max_system_error_ratio: float


@dataclass(frozen=True, slots=True)
class HardwareSpec:
    """单个部署单元的 requests/limits；请求不得超过限制。"""

    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mb: int
    memory_limit_mb: int


@dataclass(frozen=True, slots=True)
class StorageBudget:
    """事件率、保留期与制品字节预算；磁盘估算须计入索引/WAL/备份。"""

    event_bytes_per_day: int
    retention_days: int
    artifact_bytes: int


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    """正式部署一致的四单元资源声明；最大副本的连接总和必须有剩余量。"""

    hardware: Mapping[str, HardwareSpec]
    process_counts: Mapping[str, int]
    replica_counts: Mapping[str, int]
    max_replica_counts: Mapping[str, int]
    pg_connection_budget: int
    fd_budget: int
    storage_budget: StorageBudget


@dataclass(frozen=True, slots=True)
class CapacityProfile:
    """一次容量验收锁定的完整 profile；hash 与原始文件摘要分开记录。"""

    workload: WorkloadProfile
    model: ModelProfile
    slo: SloProfile | None
    resources: ResourceProfile | None


def load_profile(path: str | Path) -> CapacityProfile:
    """读取真实 YAML 文件并严格解析；不读环境变量、不解析密钥、不做任何网络调用。"""
    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"无法读取容量验收 profile {file_path}：{exc}") from exc
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = "" if mark is None else f"（第 {mark.line + 1} 行第 {mark.column + 1} 列）"
        # 不链接原始异常：PyYAML 的报错会带出源码片段，可能含密钥原文。
        raise ProfileError(f"容量验收 profile YAML 解析失败 {file_path}{location}") from None
    return parse_profile(document)


def parse_profile(document: object) -> CapacityProfile:
    """严格解析完整文档；未知键、bool 冒充整数、非有限数、缺失必填一律显式拒绝。"""
    _reject_secrets(document, "profile")
    mapping = _object("profile", document)
    _reject_unknown(mapping, _TOP_LEVEL_KEYS, "profile")
    workload = _parse_workload(_require(mapping, "workload", "profile"))
    model = _parse_model(_require(mapping, "model", "profile"))
    slo = _parse_slo(mapping["slo"]) if "slo" in mapping else None
    resources = _parse_resources(mapping["resources"]) if "resources" in mapping else None
    return CapacityProfile(workload=workload, model=model, slo=slo, resources=resources)


def acceptance_blockers(profile: CapacityProfile) -> tuple[str, ...]:
    """列出拒绝给出容量验收 pass 的真实理由（空元组表示可给 pass）。"""
    blockers: list[str] = []
    if profile.workload.users < _MIN_ACCEPTANCE_USERS:
        blockers.append(
            f"workload.users={profile.workload.users} 小于 {_MIN_ACCEPTANCE_USERS}："
            "1000 用户目标不能用空闲连接代替"
        )
    if profile.slo is None:
        blockers.append("缺少 slo：正式验收必填且无默认，只能产出诊断结果")
    if profile.resources is None:
        blockers.append("缺少 resources：正式验收必填且无默认（requests/limits、连接/FD/存储预算）")
    return tuple(blockers)


def is_acceptance_ready(profile: CapacityProfile) -> bool:
    """是否满足正式验收的 profile 前提（不代表已实测达标）。"""
    return not acceptance_blockers(profile)


def require_formal_acceptance(profile: CapacityProfile) -> None:
    """正式验收前调用：缺任一必填项抛 `ProfileAcceptanceError`，绝不静默给 pass。"""
    blockers = acceptance_blockers(profile)
    if blockers:
        raise ProfileAcceptanceError("容量验收 profile 不满足正式验收要求：" + "；".join(blockers))


def referenced_env_vars(profile: CapacityProfile) -> tuple[str, ...]:
    """REAL 模式引用的环境变量名（去掉 `env:` 前缀）；本模块不读取它们的值。"""
    names = [
        reference.split(":", 1)[1]
        for entry in profile.model.credentials_and_quotas
        for reference in (entry.credential_ref, entry.quota_group_ref)
        if reference is not None
    ]
    return tuple(dict.fromkeys(names))


def profile_hash(profile: CapacityProfile) -> str:
    """内容 hash：冻结一次测量所用的 profile，与文件格式/注释无关。"""
    canonical = json.dumps(
        profile_payload(profile), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_digest(path: str | Path) -> str:
    """原始文件字节的 sha256：报告里记录逐字节锁定的那份配置。"""
    file_path = Path(path)
    try:
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProfileError(f"无法读取容量验收 profile {file_path}：{exc}") from exc


def profile_payload(profile: CapacityProfile) -> dict[str, object]:
    """可回灌 `parse_profile` 的规范文档；键序固定，供报告与 hash 使用。"""
    payload: dict[str, object] = {
        "workload": _workload_payload(profile.workload),
        "model": _model_payload(profile.model),
    }
    if profile.slo is not None:
        payload["slo"] = {
            "admission_p95_ms": profile.slo.admission_p95_ms,
            "admission_p99_ms": profile.slo.admission_p99_ms,
            "framework_first_delta_p95_ms": profile.slo.framework_first_delta_p95_ms,
            "recovery_max_sec": profile.slo.recovery_max_sec,
            "max_system_error_ratio": profile.slo.max_system_error_ratio,
        }
    if profile.resources is not None:
        payload["resources"] = _resources_payload(profile.resources)
    return payload


def _workload_payload(workload: WorkloadProfile) -> dict[str, object]:
    attachment: dict[str, object] = {"ratio": workload.attachment_distribution.ratio}
    upload_bytes = workload.attachment_distribution.upload_bytes
    if upload_bytes is not None:
        attachment["upload_bytes"] = _distribution_payload(upload_bytes)
    return {
        "users": workload.users,
        "active_runs_per_user": workload.active_runs_per_user,
        "ramp_sec": workload.ramp_sec,
        "steady_sec": workload.steady_sec,
        "turns_per_user": workload.turns_per_user,
        "input_distribution": {
            "think_time_ms": _distribution_payload(workload.input_distribution.think_time_ms),
            "text_bytes": _distribution_payload(workload.input_distribution.text_bytes),
        },
        "attachment_distribution": attachment,
    }


def _model_payload(model: ModelProfile) -> dict[str, object]:
    if model.mode == "CONTROLLED_PROBE":
        if model.probe_latency_ms is None or model.output_distribution is None:
            raise ProfileError("model.mode=CONTROLLED_PROBE 必须声明 probe_latency_ms 与 output_distribution")
        return {
            "mode": model.mode,
            "probe_latency_ms": _distribution_payload(model.probe_latency_ms),
            "output_distribution": _distribution_payload(model.output_distribution),
        }
    entries: list[dict[str, object]] = []
    for entry in model.credentials_and_quotas:
        item: dict[str, object] = {
            "provider": entry.provider,
            "model": entry.model,
            "credential_ref": entry.credential_ref,
            "max_inflight": entry.max_inflight,
            "rpm": entry.rpm,
            "tpm": entry.tpm,
        }
        if entry.quota_group_ref is not None:
            item["quota_group_ref"] = entry.quota_group_ref
        entries.append(item)
    return {"mode": model.mode, "credentials_and_quotas": entries}


def _resources_payload(resources: ResourceProfile) -> dict[str, object]:
    return {
        "hardware": {
            unit: {
                "cpu_request_millicores": resources.hardware[unit].cpu_request_millicores,
                "cpu_limit_millicores": resources.hardware[unit].cpu_limit_millicores,
                "memory_request_mb": resources.hardware[unit].memory_request_mb,
                "memory_limit_mb": resources.hardware[unit].memory_limit_mb,
            }
            for unit in _UNITS
        },
        "process_counts": {unit: resources.process_counts[unit] for unit in _UNITS},
        "replica_counts": {unit: resources.replica_counts[unit] for unit in _UNITS},
        "max_replica_counts": {unit: resources.max_replica_counts[unit] for unit in _UNITS},
        "pg_connection_budget": resources.pg_connection_budget,
        "fd_budget": resources.fd_budget,
        "storage_budget": {
            "event_bytes_per_day": resources.storage_budget.event_bytes_per_day,
            "retention_days": resources.storage_budget.retention_days,
            "artifact_bytes": resources.storage_budget.artifact_bytes,
        },
    }


def _distribution_payload(item: PercentileDistribution) -> dict[str, int]:
    return {"p50": item.p50, "p95": item.p95, "max": item.max}


def _parse_workload(value: object) -> WorkloadProfile:
    path = "workload"
    mapping = _object(path, value)
    _reject_unknown(
        mapping,
        (
            "users",
            "active_runs_per_user",
            "ramp_sec",
            "steady_sec",
            "turns_per_user",
            "input_distribution",
            "attachment_distribution",
        ),
        path,
    )
    users = _int(f"{path}.users", _require(mapping, "users", path), minimum=1)
    active = _int(
        f"{path}.active_runs_per_user", _require(mapping, "active_runs_per_user", path), minimum=1,
    )
    if active > users:
        raise ProfileError(f"{path}.active_runs_per_user 不得超过 {path}.users（{users}）")
    return WorkloadProfile(
        users=users,
        active_runs_per_user=active,
        ramp_sec=_int(f"{path}.ramp_sec", _require(mapping, "ramp_sec", path), minimum=0),
        steady_sec=_int(f"{path}.steady_sec", _require(mapping, "steady_sec", path), minimum=1),
        turns_per_user=_int(
            f"{path}.turns_per_user", _require(mapping, "turns_per_user", path), minimum=1,
        ),
        input_distribution=_parse_input_distribution(
            f"{path}.input_distribution", _require(mapping, "input_distribution", path),
        ),
        attachment_distribution=_parse_attachment_distribution(
            f"{path}.attachment_distribution", _require(mapping, "attachment_distribution", path),
        ),
    )


def _parse_input_distribution(path: str, value: object) -> InputDistribution:
    mapping = _object(path, value)
    _reject_unknown(mapping, ("think_time_ms", "text_bytes"), path)
    return InputDistribution(
        think_time_ms=_distribution(
            f"{path}.think_time_ms", _require(mapping, "think_time_ms", path),
        ),
        text_bytes=_distribution(
            f"{path}.text_bytes", _require(mapping, "text_bytes", path), minimum_max=1,
        ),
    )


def _parse_attachment_distribution(path: str, value: object) -> AttachmentDistribution:
    mapping = _object(path, value)
    _reject_unknown(mapping, ("ratio", "upload_bytes"), path)
    ratio = _number(f"{path}.ratio", _require(mapping, "ratio", path), minimum=0.0, maximum=1.0)
    raw_upload = mapping.get("upload_bytes")
    if raw_upload is None:
        if ratio > 0:
            raise ProfileError(f"{path}.upload_bytes 在 {path}.ratio>0 时必填（明确附件大小分布）")
        return AttachmentDistribution(ratio=ratio, upload_bytes=None)
    return AttachmentDistribution(
        ratio=ratio,
        upload_bytes=_distribution(f"{path}.upload_bytes", raw_upload, minimum_max=1),
    )


def _parse_model(value: object) -> ModelProfile:
    path = "model"
    mapping = _object(path, value)
    mode = _text(f"{path}.mode", _require(mapping, "mode", path))
    if mode == "CONTROLLED_PROBE":
        _reject_unknown(mapping, ("mode", "probe_latency_ms", "output_distribution"), path)
        return ModelProfile(
            mode=mode,
            probe_latency_ms=_distribution(
                f"{path}.probe_latency_ms", _require(mapping, "probe_latency_ms", path),
            ),
            output_distribution=_distribution(
                f"{path}.output_distribution",
                _require(mapping, "output_distribution", path),
                minimum_max=1,
            ),
            credentials_and_quotas=(),
        )
    if mode == "REAL":
        _reject_unknown(mapping, ("mode", "credentials_and_quotas"), path)
        raw_entries = _sequence(
            f"{path}.credentials_and_quotas", _require(mapping, "credentials_and_quotas", path),
        )
        if not raw_entries:
            raise ProfileError(f"{path}.credentials_and_quotas 必须至少声明一项凭据/额度证据")
        return ModelProfile(
            mode=mode,
            probe_latency_ms=None,
            output_distribution=None,
            credentials_and_quotas=tuple(
                _parse_quota_entry(f"{path}.credentials_and_quotas[{index}]", item)
                for index, item in enumerate(raw_entries)
            ),
        )
    raise ProfileError(f"{path}.mode 必须是 CONTROLLED_PROBE 或 REAL，收到 {mode!r}")


def _parse_quota_entry(path: str, value: object) -> QuotaEntry:
    mapping = _object(path, value)
    _reject_unknown(
        mapping,
        ("provider", "model", "credential_ref", "quota_group_ref", "max_inflight", "rpm", "tpm"),
        path,
    )
    quota_group_ref = mapping.get("quota_group_ref")
    rpm = _optional_positive_int(f"{path}.rpm", mapping.get("rpm"))
    tpm = _optional_positive_int(f"{path}.tpm", mapping.get("tpm"))
    if rpm is None and tpm is None:
        raise ProfileError(
            f"{path} 必须至少声明 rpm 或 tpm 之一：null 只关闭该维度应用限流，不能当作额度证据"
        )
    return QuotaEntry(
        provider=_text(f"{path}.provider", _require(mapping, "provider", path)),
        model=_text(f"{path}.model", _require(mapping, "model", path)),
        credential_ref=_env_reference(
            f"{path}.credential_ref", _require(mapping, "credential_ref", path),
        ),
        quota_group_ref=(
            None if quota_group_ref is None else _env_reference(f"{path}.quota_group_ref", quota_group_ref)
        ),
        max_inflight=_int(
            f"{path}.max_inflight", _require(mapping, "max_inflight", path), minimum=1,
        ),
        rpm=rpm,
        tpm=tpm,
    )


def _parse_slo(value: object) -> SloProfile:
    path = "slo"
    mapping = _object(path, value)
    _reject_unknown(mapping, _SLO_KEYS, path)
    admission_p95 = _positive_number(
        f"{path}.admission_p95_ms", _require(mapping, "admission_p95_ms", path),
    )
    admission_p99 = _positive_number(
        f"{path}.admission_p99_ms", _require(mapping, "admission_p99_ms", path),
    )
    if admission_p95 > admission_p99:
        raise ProfileError(
            f"{path}.admission_p95_ms 不得超过 {path}.admission_p99_ms（{admission_p99:g}）"
        )
    return SloProfile(
        admission_p95_ms=admission_p95,
        admission_p99_ms=admission_p99,
        framework_first_delta_p95_ms=_positive_number(
            f"{path}.framework_first_delta_p95_ms",
            _require(mapping, "framework_first_delta_p95_ms", path),
        ),
        recovery_max_sec=_positive_number(
            f"{path}.recovery_max_sec", _require(mapping, "recovery_max_sec", path),
        ),
        max_system_error_ratio=_number(
            f"{path}.max_system_error_ratio",
            _require(mapping, "max_system_error_ratio", path),
            minimum=0.0,
            maximum=1.0,
        ),
    )


def _parse_resources(value: object) -> ResourceProfile:
    path = "resources"
    mapping = _object(path, value)
    _reject_unknown(
        mapping,
        (
            "hardware",
            "process_counts",
            "replica_counts",
            "max_replica_counts",
            "pg_connection_budget",
            "fd_budget",
            "storage_budget",
        ),
        path,
    )
    hardware = _parse_hardware(f"{path}.hardware", _require(mapping, "hardware", path))
    process_counts = _unit_counts(f"{path}.process_counts", _require(mapping, "process_counts", path))
    replica_counts = _unit_counts(f"{path}.replica_counts", _require(mapping, "replica_counts", path))
    max_replica_counts = _unit_counts(
        f"{path}.max_replica_counts", _require(mapping, "max_replica_counts", path),
    )
    _validate_replica_room(path, replica_counts, max_replica_counts)
    _validate_single_pod_units(path, max_replica_counts)
    return ResourceProfile(
        hardware=hardware,
        process_counts=process_counts,
        replica_counts=replica_counts,
        max_replica_counts=max_replica_counts,
        pg_connection_budget=_int(
            f"{path}.pg_connection_budget", _require(mapping, "pg_connection_budget", path), minimum=1,
        ),
        fd_budget=_int(f"{path}.fd_budget", _require(mapping, "fd_budget", path), minimum=1),
        storage_budget=_parse_storage_budget(
            f"{path}.storage_budget", _require(mapping, "storage_budget", path),
        ),
    )


def _validate_replica_room(
    path: str, replica_counts: Mapping[str, int], max_replica_counts: Mapping[str, int],
) -> None:
    for unit in _UNITS:
        if replica_counts[unit] > max_replica_counts[unit]:
            raise ProfileError(
                f"{path}.replica_counts.{unit} 不得超过 "
                f"{path}.max_replica_counts.{unit}（{max_replica_counts[unit]}）"
            )


def _validate_single_pod_units(path: str, max_replica_counts: Mapping[str, int]) -> None:
    for unit in _SINGLE_POD_UNITS:
        if max_replica_counts[unit] != 1:
            raise ProfileError(
                f"{path}.max_replica_counts.{unit} 必须为 1（Console/Gateway 各一个 Pod 是部署约束）"
            )


def _parse_hardware(path: str, value: object) -> dict[str, HardwareSpec]:
    mapping = _object(path, value)
    _require_units(path, mapping)
    return {unit: _parse_hardware_spec(f"{path}.{unit}", mapping[unit]) for unit in _UNITS}


def _parse_hardware_spec(path: str, value: object) -> HardwareSpec:
    mapping = _object(path, value)
    _reject_unknown(
        mapping,
        ("cpu_request_millicores", "cpu_limit_millicores", "memory_request_mb", "memory_limit_mb"),
        path,
    )
    cpu_request = _int(
        f"{path}.cpu_request_millicores", _require(mapping, "cpu_request_millicores", path), minimum=1,
    )
    cpu_limit = _int(
        f"{path}.cpu_limit_millicores", _require(mapping, "cpu_limit_millicores", path), minimum=1,
    )
    if cpu_request > cpu_limit:
        raise ProfileError(
            f"{path}.cpu_request_millicores 不得超过 {path}.cpu_limit_millicores（{cpu_limit}）"
        )
    memory_request = _int(
        f"{path}.memory_request_mb", _require(mapping, "memory_request_mb", path), minimum=1,
    )
    memory_limit = _int(f"{path}.memory_limit_mb", _require(mapping, "memory_limit_mb", path), minimum=1)
    if memory_request > memory_limit:
        raise ProfileError(f"{path}.memory_request_mb 不得超过 {path}.memory_limit_mb（{memory_limit}）")
    return HardwareSpec(
        cpu_request_millicores=cpu_request,
        cpu_limit_millicores=cpu_limit,
        memory_request_mb=memory_request,
        memory_limit_mb=memory_limit,
    )


def _parse_storage_budget(path: str, value: object) -> StorageBudget:
    mapping = _object(path, value)
    _reject_unknown(mapping, ("event_bytes_per_day", "retention_days", "artifact_bytes"), path)
    return StorageBudget(
        event_bytes_per_day=_int(
            f"{path}.event_bytes_per_day", _require(mapping, "event_bytes_per_day", path), minimum=1,
        ),
        retention_days=_int(
            f"{path}.retention_days", _require(mapping, "retention_days", path), minimum=1, maximum=3650,
        ),
        artifact_bytes=_int(
            f"{path}.artifact_bytes", _require(mapping, "artifact_bytes", path), minimum=0,
        ),
    )


def _unit_counts(path: str, value: object) -> dict[str, int]:
    mapping = _object(path, value)
    _require_units(path, mapping)
    return {unit: _int(f"{path}.{unit}", mapping[unit], minimum=1) for unit in _UNITS}


def _require_units(path: str, mapping: Mapping[str, object]) -> None:
    _reject_unknown(mapping, _UNITS, path)
    missing = [unit for unit in _UNITS if unit not in mapping]
    if missing:
        raise ProfileError(f"{path} 缺少部署单元: {', '.join(missing)}")


def _distribution(path: str, value: object, *, minimum_max: int = 0) -> PercentileDistribution:
    mapping = _object(path, value)
    _reject_unknown(mapping, ("p50", "p95", "max"), path)
    p50 = _int(f"{path}.p50", _require(mapping, "p50", path), minimum=0)
    p95 = _int(f"{path}.p95", _require(mapping, "p95", path), minimum=0)
    maximum = _int(f"{path}.max", _require(mapping, "max", path), minimum=0)
    if p95 < p50:
        raise ProfileError(f"{path}.p95 必须 ≥ {path}.p50（{p50}）")
    if maximum < p95:
        raise ProfileError(f"{path}.max 必须 ≥ {path}.p95（{p95}）")
    if maximum < minimum_max:
        raise ProfileError(f"{path}.max 必须 ≥ {minimum_max}")
    return PercentileDistribution(p50=p50, p95=p95, max=maximum)


def _object(path: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{path} 必须是对象，收到 {type(value).__name__}")
    for index, key in enumerate(cast("Mapping[object, object]", value)):
        if not isinstance(key, str):
            raise ProfileError(f"{path} 的第 {index + 1} 个键必须是字符串（不回带键原文）")
    return cast("Mapping[str, object]", value)


def _sequence(path: str, value: object) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProfileError(f"{path} 必须是列表")
    return cast("Sequence[object]", value)


def _require(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        raise ProfileError(f"{path} 缺少必填键 {key}")
    return mapping[key]


def _reject_unknown(mapping: Mapping[str, object], allowed: Sequence[str], path: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ProfileError(f"{path} 含未知键: {', '.join(unknown)}")


def _int(path: str, value: object, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(f"{path} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"必须 ≥ {minimum}" if maximum is None else f"必须在 [{minimum}, {maximum}] 内"
        raise ProfileError(f"{path} {bound}")
    return value


def _optional_positive_int(path: str, value: object) -> int | None:
    if value is None:
        return None
    return _int(path, value, minimum=1)


def _positive_number(path: str, value: object) -> float:
    return _number(path, value, minimum=0.0, exclusive_minimum=True)


def _number(
    path: str,
    value: object,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{path} 必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ProfileError(f"{path} 必须是有限数")
    if minimum is not None and (number <= minimum if exclusive_minimum else number < minimum):
        raise ProfileError(f"{path} 必须 {'>' if exclusive_minimum else '≥'} {minimum:g}")
    if maximum is not None and number > maximum:
        raise ProfileError(f"{path} 必须 ≤ {maximum:g}")
    return number


def _text(path: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{path} 必须是非空字符串")
    return value


def _env_reference(path: str, value: object) -> str:
    reference = _text(path, value)
    if _ENV_REFERENCE.fullmatch(reference) is None:
        raise ProfileError(
            f"{path} 必须是不含密钥原文的 env: 引用（如 env:MUAD_CAPACITY_MODEL_KEY）"
        )
    return reference


def _looks_like_credential_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SECRET_KEY_NAMES:
        return True
    return any(word in _SECRET_KEY_WORDS for word in re.split(r"[^a-z0-9]+", lowered))


def _looks_like_secret_value(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_VALUE_PATTERNS)


def _reject_secrets(node: object, path: str) -> None:
    """fail-closed：凭据字段与密钥形态的值一律拒绝，且错误消息不回带原文。"""
    if isinstance(node, Mapping):
        for key, item in node.items():
            if isinstance(key, str) and _looks_like_credential_key(key):
                raise ProfileError(
                    f"{path}.{key} 是凭据字段：容量 profile 只能声明 env: 引用，不得内嵌密钥"
                )
            _reject_secrets(item, f"{path}.{key}")
        return
    if isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for index, item in enumerate(node):
            _reject_secrets(item, f"{path}[{index}]")
        return
    if isinstance(node, str) and _looks_like_secret_value(node):
        raise ProfileError(f"{path} 疑似内嵌真实密钥：容量 profile 不得包含密钥原文（错误不回带原文）")
