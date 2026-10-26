"""容量报告生成（TASK-117；设计 §4.1 部署与容量 / §4.3 分层验收与报告）。

只用实际运行证据生成 P95/P99、瓶颈、资源预算、配置 hash 与 Runtime/Worker 副本建议：

- **证据包**：`CapacityEvidence` 是 typed、可 JSON 往返、严格校验的；报告只消费证据包，
  不从 profile/清单/环境变量另取默认值。证据来自真实负载报告（TASK-109 驱动）、真实进程 env
  （执行槽）、真实 engine 工厂（PG 每进程连接上限）与真实渲染清单（部署事实，TASK-104 路径）。
- **缺证据即失败**：没有样本就没有分位数（`EvidenceError`）；没有执行事实就不给 Pod 数；
  缺 SLO/资源声明或受限规模运行只能给 `DIAGNOSTIC` 结论，绝不输出生产达标。
- **边界分开**：`CONTROLLED_PROBE` 的吞吐不冒充真实模型吞吐；真实供方额度未验证时如实标注。
- **公式**：`N = ceil(目标执行并发 / 单 Pod 容量 / 目标利用率) + 冗余副本`（§4.1），参数全部来自
  证据与业务确认，`ReplicaRecommendation.formula` 给出可逐字复算的算式。

用法（运营口径见 `docs/operations/agent-capacity-webchat-capacity.md`）：

    uv run python -m tests.capacity.report --evidence <bundle.json> \
        --pg-maintenance-reserve 10 --runtime-target 16 --worker-target 8 \
        --utilization 0.8 --redundancy 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from scripts import check_agent_capacity_deployment as preflight
from tests.capacity.driver import FailureKind, LoadReport, RunKind
from tests.capacity.metrics import MetricsError, summarize_latencies
from tests.capacity.profile import CapacityProfile, HardwareSpec, ResourceProfile

__all__ = [
    "AUTOSCALED_UNITS",
    "DB_UNITS",
    "UNITS",
    "Bottleneck",
    "BottleneckKind",
    "CapacityEvidence",
    "CapacityReport",
    "DeploymentFacts",
    "EvidenceError",
    "ExecutionEvidence",
    "LatencyEvidence",
    "ModelBoundary",
    "ModelMode",
    "PercentileFact",
    "PostgresEvidence",
    "ReplicaRecommendation",
    "ReplicaTarget",
    "ReportError",
    "ResourceBudgetLine",
    "ResourceEvidence",
    "SharedBudgetFacts",
    "SloEvidence",
    "SloResult",
    "UnitDeploymentFacts",
    "Verdict",
    "WorkloadEvidence",
    "build_report",
    "capture_evidence",
    "config_hash",
    "deployment_digest",
    "deployment_facts_from_manifests",
    "deployment_payload",
    "evidence_payload",
    "load_evidence",
    "main",
    "parse_evidence",
    "recommend_replicas",
    "render_markdown",
    "write_evidence",
]

#: 固定四个部署单元（harness-arch#RULE-arch-001）；前端聊天不新增工作负载。
UNITS: tuple[str, ...] = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
#: 可横向扩展的单元（HPA 提供上界）；Console/Gateway 各 1 Pod 是部署约束。
AUTOSCALED_UNITS: tuple[str, ...] = ("agent-runtime", "agent-worker")
#: 持库单元（Gateway 不持库）：PG 总连接按它们求和。
DB_UNITS: tuple[str, ...] = ("console-platform", "agent-runtime", "agent-worker")
#: 报告必须持有的延迟样本（受理延迟是容量报告的最小证据）。
REQUIRED_METRICS: tuple[str, ...] = ("admission_ms",)
#: 系统错误（框架/传输/终态缺失）与明确过载（拒绝/超时）分开计数：不靠排除失败制造成功率。
SYSTEM_ERROR_KINDS: frozenset[str] = frozenset(
    {
        FailureKind.CONVERSATION_REJECTED.value,
        FailureKind.TRANSPORT_ERROR.value,
        FailureKind.SSE_REJECTED.value,
        FailureKind.SSE_ERROR.value,
        FailureKind.TERMINAL_MISSING.value,
        FailureKind.RUN_FAILED.value,
    }
)
OVERLOAD_KINDS: frozenset[str] = frozenset(
    {
        FailureKind.ADMISSION_REJECTED.value,
        FailureKind.ADMISSION_TIMEOUT.value,
        FailureKind.SSE_TIMEOUT.value,
        FailureKind.USER_DEADLINE_EXCEEDED.value,
    }
)
#: 副本建议的容量口径：实测压满（MEASURED）或仅按真实声明配置（DECLARED，如实标注未压满）。
CAPACITY_BASIS_MEASURED = "MEASURED"
CAPACITY_BASIS_DECLARED = "DECLARED"

_ENV_REFERENCE = re.compile(r"env:[A-Z][A-Z0-9_]{0,63}")
_POOL_KEY = re.compile(r"[A-Z]+_HTTP_MAX_CONNECTIONS")
_CPU_QUANTITY = re.compile(r"(?P<millicores>[0-9]+)m|(?P<cores>[0-9]+(?:\.[0-9]+)?)")
_MEMORY_QUANTITY = re.compile(r"(?P<value>[0-9]+)(?P<suffix>Mi|Gi)")
_EVIDENCE_KEYS = (
    "captured_at",
    "run_kind",
    "profile_hash",
    "profile_file_digest",
    "config_hash",
    "workload",
    "latencies",
    "failure_counts",
    "execution",
    "postgres",
    "model",
    "deployment",
    "resources",
    "slo",
    "shared",
)


class ReportError(RuntimeError):
    """报告无法给出结论（入参非法、清单渲染失败、部署事实缺失）：显式失败，不回落默认。"""


class EvidenceError(ReportError):
    """证据包缺失/非法/被篡改：没有证据就不给数字，更不给生产达标结论。"""


class Verdict(StrEnum):
    """报告结论：生产达标 / 明确不达标 / 只能诊断（不足以给生产结论）。"""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DIAGNOSTIC = "DIAGNOSTIC"


class ModelMode(StrEnum):
    """模型边界：受控 HTTP 探针或真实模型（供方额度证据按 env: 引用）。"""

    CONTROLLED_PROBE = "CONTROLLED_PROBE"
    REAL = "REAL"


class BottleneckKind(StrEnum):
    """瓶颈类别：本次运行真实触到的上限（没触到就不产出）。"""

    EXECUTION_SLOTS = "EXECUTION_SLOTS"
    MODEL_INFLIGHT = "MODEL_INFLIGHT"
    ADMISSION_QUEUE = "ADMISSION_QUEUE"
    PG_CONNECTIONS = "PG_CONNECTIONS"


# --------------------------------------------------------------------------------------
# 证据包：typed、可 JSON 往返、严格校验
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkloadEvidence:
    """本次负载形状：锁定 profile 的目标 + 真实展开的规模与尝试数。"""

    users: int
    active_runs_per_user: int
    ramp_sec: int
    steady_sec: int
    turns_per_user: int
    effective_users: int
    attempts: int
    scale_users: int | None


@dataclass(frozen=True, slots=True)
class LatencyEvidence:
    """一条延迟指标的真实样本（毫秒）；空样本不是证据，解析期即拒绝。"""

    metric: str
    samples_ms: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SloEvidence:
    """锁定 profile 的 SLO 阈值（无默认值）：报告逐项判定并记录。"""

    admission_p95_ms: float
    admission_p99_ms: float
    framework_first_delta_p95_ms: float
    recovery_max_sec: float
    max_system_error_ratio: float


@dataclass(frozen=True, slots=True)
class ModelBoundary:
    """模型边界证据：模式 + 真实观测（调用数/最大并发/故障）与额度上限。"""

    mode: ModelMode
    credential_refs: tuple[str, ...]
    declared_max_inflight: int | None
    observed_calls: int
    observed_max_concurrency: int
    faults: Mapping[str, int]

    @property
    def real_supplier_capacity(self) -> bool:
        """是否具备真实供方额度证据（探针模式永远为 False：探针吞吐不冒充真实容量）。"""
        return self.mode is ModelMode.REAL and bool(self.credential_refs)


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """执行事实：真实进程执行槽、测量副本数、并发采样峰值（Run 级）。"""

    slots_per_process: Mapping[str, int]
    measured_replicas: Mapping[str, int]
    peak_running_runs: int
    peak_waiting_runs: int
    peak_admitted_runs: int
    peak_active_streams: int
    sample_count: int
    wall_seconds: float


@dataclass(frozen=True, slots=True)
class PostgresEvidence:
    """PG 真实观测：连接峰值、锁等待、WAL 增长与库大小。"""

    connections_peak: int
    connections_active_peak: int
    locks_waiting_peak: int
    wal_growth_bytes: int
    database_bytes: int


@dataclass(frozen=True, slots=True)
class ResourceEvidence:
    """容量 profile 的资源声明 + 真实 engine 工厂的每进程连接上限。"""

    hardware: Mapping[str, HardwareSpec]
    process_counts: Mapping[str, int]
    replica_counts: Mapping[str, int]
    max_replica_counts: Mapping[str, int]
    pg_connection_budget: int
    fd_budget: int
    pg_connections_per_process: Mapping[str, int]
    storage_event_bytes_per_day: int
    storage_retention_days: int
    storage_artifact_bytes: int


@dataclass(frozen=True, slots=True)
class SharedBudgetFacts:
    """真实 typed 启动预算（进程级共享额度；本仓库四单元共用一套 SharedSettings）。"""

    redis_max_connections: int
    inbound_subscriber_limits: Mapping[str, int]
    sse_total_buffer_bytes: int


@dataclass(frozen=True, slots=True)
class UnitDeploymentFacts:
    """一个部署单元的真实渲染清单事实（HPA 上界、requests/limits、执行槽、出站池）。"""

    replicas: int
    max_replicas: int
    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mb: int
    memory_limit_mb: int
    termination_grace_sec: int
    execution_concurrency: int | None
    outbound_connections_per_process: int


@dataclass(frozen=True, slots=True)
class DeploymentFacts:
    """一次渲染的部署事实（渲染来源 + 四单元）：配置 hash 的一半输入。"""

    renderer: str
    units: Mapping[str, UnitDeploymentFacts]


@dataclass(frozen=True, slots=True)
class CapacityEvidence:
    """一次真实测量的完整证据包；报告只消费它（落盘/读回逐字段相等）。"""

    captured_at: str
    run_kind: RunKind
    profile_hash: str
    profile_file_digest: str
    config_hash: str
    workload: WorkloadEvidence
    latencies: tuple[LatencyEvidence, ...]
    failure_counts: Mapping[str, int]
    execution: ExecutionEvidence
    postgres: PostgresEvidence
    model: ModelBoundary
    deployment: DeploymentFacts
    resources: ResourceEvidence | None
    slo: SloEvidence | None
    shared: SharedBudgetFacts


# --------------------------------------------------------------------------------------
# 报告结构
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PercentileFact:
    """一条延迟指标的分位数事实（最近秩，可复算）。"""

    metric: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True, slots=True)
class SloResult:
    """一项 SLO 判定：观测值、阈值与结论（缺观测值时不产出，改记阻塞项）。"""

    metric: str
    observed: float
    threshold: float
    passed: bool


@dataclass(frozen=True, slots=True)
class Bottleneck:
    """一个真实触到的瓶颈：观测值、上限与是否压满。"""

    kind: BottleneckKind
    unit: str | None
    observed: float
    ceiling: float
    exhausted: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ResourceBudgetLine:
    """一个单元的资源预算行：清单 requests/limits + PG 连接份额 + 每进程 FD 上限。"""

    unit: str
    cpu_request_millicores: int
    cpu_limit_millicores: int
    memory_request_mb: int
    memory_limit_mb: int
    pg_connections_at_max_replicas: int
    fd_per_process: int
    within_pg_budget: bool
    within_fd_budget: bool
    within_budget: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReplicaRecommendation:
    """一个单元的副本建议：输入、单 Pod 容量、公式与部署上界核对。"""

    unit: str
    target_execution_concurrency: int
    per_pod_capacity: int
    target_utilization: float
    redundancy_replicas: int
    recommended_replicas: int
    max_replicas: int
    within_max_replicas: bool
    capacity_basis: str
    formula: str


@dataclass(frozen=True, slots=True)
class CapacityReport:
    """一次容量报告的完整结论；所有数字都能从证据包复算。"""

    captured_at: str
    config_hash: str
    profile_hash: str
    run_kind: RunKind
    verdict: Verdict
    latencies: tuple[PercentileFact, ...]
    slo: tuple[SloResult, ...]
    error_counts: Mapping[str, int]
    error_ratio: float
    bottlenecks: tuple[Bottleneck, ...]
    resources: tuple[ResourceBudgetLine, ...]
    replicas: tuple[ReplicaRecommendation, ...]
    model_boundary: ModelBoundary
    blockers: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplicaTarget:
    """业务确认的副本建议参数（无默认值）：目标执行并发、目标利用率与冗余副本。"""

    unit: str
    target_execution_concurrency: int
    target_utilization: float
    redundancy_replicas: int

    def __post_init__(self) -> None:
        if self.unit not in AUTOSCALED_UNITS:
            raise EvidenceError(
                f"{self.unit} 不是可横向扩展单元（{AUTOSCALED_UNITS}）：单 Pod 约束不产副本建议"
            )
        if self.target_execution_concurrency < 1:
            raise EvidenceError(f"目标执行并发必须 ≥ 1，收到 {self.target_execution_concurrency}")
        if not 0.0 < self.target_utilization <= 1.0:
            raise EvidenceError(f"目标利用率必须落在 (0, 1]，收到 {self.target_utilization!r}")
        if self.redundancy_replicas < 0:
            raise EvidenceError(f"冗余副本必须 ≥ 0，收到 {self.redundancy_replicas}")


# --------------------------------------------------------------------------------------
# 证据包序列化 / 严格解析
# --------------------------------------------------------------------------------------


def deployment_payload(facts: DeploymentFacts) -> dict[str, object]:
    """部署事实的规范载荷（键序稳定，供配置 hash 与 JSON 往返使用）。"""
    return {
        "renderer": facts.renderer,
        "units": {
            unit: {
                "replicas": item.replicas,
                "max_replicas": item.max_replicas,
                "cpu_request_millicores": item.cpu_request_millicores,
                "cpu_limit_millicores": item.cpu_limit_millicores,
                "memory_request_mb": item.memory_request_mb,
                "memory_limit_mb": item.memory_limit_mb,
                "termination_grace_sec": item.termination_grace_sec,
                "execution_concurrency": item.execution_concurrency,
                "outbound_connections_per_process": item.outbound_connections_per_process,
            }
            for unit, item in sorted(facts.units.items())
        },
    }


def deployment_digest(facts: DeploymentFacts) -> str:
    """部署事实的内容摘要（sha256；与文件格式/注释无关）。"""
    canonical = json.dumps(
        deployment_payload(facts), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def config_hash(profile_hash_value: str, deployment: DeploymentFacts) -> str:
    """配置 hash = profile 内容 hash + 部署事实摘要（§3.7「每次测量前锁定一份配置并保存 hash」）。"""
    canonical = f"profile:{profile_hash_value}\ndeployment:{deployment_digest(deployment)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evidence_payload(evidence: CapacityEvidence) -> dict[str, object]:
    """证据包的 JSON 载荷（列表/对象；`parse_evidence` 可严格读回）。"""
    return {
        "captured_at": evidence.captured_at,
        "run_kind": evidence.run_kind.value,
        "profile_hash": evidence.profile_hash,
        "profile_file_digest": evidence.profile_file_digest,
        "config_hash": evidence.config_hash,
        "workload": _workload_payload(evidence.workload),
        "latencies": [
            {"metric": item.metric, "samples_ms": list(item.samples_ms)}
            for item in evidence.latencies
        ],
        "failure_counts": dict(evidence.failure_counts),
        "execution": _execution_payload(evidence.execution),
        "postgres": _postgres_payload(evidence.postgres),
        "model": _model_payload(evidence.model),
        "deployment": deployment_payload(evidence.deployment),
        "resources": (
            None if evidence.resources is None else _resources_payload(evidence.resources)
        ),
        "slo": None if evidence.slo is None else _slo_payload(evidence.slo),
        "shared": _shared_payload(evidence.shared),
    }


def _workload_payload(workload: WorkloadEvidence) -> dict[str, object]:
    return {
        "users": workload.users,
        "active_runs_per_user": workload.active_runs_per_user,
        "ramp_sec": workload.ramp_sec,
        "steady_sec": workload.steady_sec,
        "turns_per_user": workload.turns_per_user,
        "effective_users": workload.effective_users,
        "attempts": workload.attempts,
        "scale_users": workload.scale_users,
    }


def _execution_payload(execution: ExecutionEvidence) -> dict[str, object]:
    return {
        "slots_per_process": dict(execution.slots_per_process),
        "measured_replicas": dict(execution.measured_replicas),
        "peak_running_runs": execution.peak_running_runs,
        "peak_waiting_runs": execution.peak_waiting_runs,
        "peak_admitted_runs": execution.peak_admitted_runs,
        "peak_active_streams": execution.peak_active_streams,
        "sample_count": execution.sample_count,
        "wall_seconds": execution.wall_seconds,
    }


def _postgres_payload(postgres: PostgresEvidence) -> dict[str, object]:
    return {
        "connections_peak": postgres.connections_peak,
        "connections_active_peak": postgres.connections_active_peak,
        "locks_waiting_peak": postgres.locks_waiting_peak,
        "wal_growth_bytes": postgres.wal_growth_bytes,
        "database_bytes": postgres.database_bytes,
    }


def _model_payload(model: ModelBoundary) -> dict[str, object]:
    return {
        "mode": model.mode.value,
        "credential_refs": list(model.credential_refs),
        "declared_max_inflight": model.declared_max_inflight,
        "observed_calls": model.observed_calls,
        "observed_max_concurrency": model.observed_max_concurrency,
        "faults": dict(model.faults),
    }


def _shared_payload(shared: SharedBudgetFacts) -> dict[str, object]:
    return {
        "redis_max_connections": shared.redis_max_connections,
        "inbound_subscriber_limits": dict(shared.inbound_subscriber_limits),
        "sse_total_buffer_bytes": shared.sse_total_buffer_bytes,
    }


def _resources_payload(resources: ResourceEvidence) -> dict[str, object]:
    return {
        "hardware": {
            unit: {
                "cpu_request_millicores": resources.hardware[unit].cpu_request_millicores,
                "cpu_limit_millicores": resources.hardware[unit].cpu_limit_millicores,
                "memory_request_mb": resources.hardware[unit].memory_request_mb,
                "memory_limit_mb": resources.hardware[unit].memory_limit_mb,
            }
            for unit in UNITS
        },
        "process_counts": {unit: resources.process_counts[unit] for unit in UNITS},
        "replica_counts": {unit: resources.replica_counts[unit] for unit in UNITS},
        "max_replica_counts": {unit: resources.max_replica_counts[unit] for unit in UNITS},
        "pg_connection_budget": resources.pg_connection_budget,
        "fd_budget": resources.fd_budget,
        "pg_connections_per_process": {
            unit: resources.pg_connections_per_process[unit] for unit in DB_UNITS
        },
        "storage_event_bytes_per_day": resources.storage_event_bytes_per_day,
        "storage_retention_days": resources.storage_retention_days,
        "storage_artifact_bytes": resources.storage_artifact_bytes,
    }


def _slo_payload(slo: SloEvidence) -> dict[str, object]:
    return {
        "admission_p95_ms": slo.admission_p95_ms,
        "admission_p99_ms": slo.admission_p99_ms,
        "framework_first_delta_p95_ms": slo.framework_first_delta_p95_ms,
        "recovery_max_sec": slo.recovery_max_sec,
        "max_system_error_ratio": slo.max_system_error_ratio,
    }


def parse_evidence(payload: object) -> CapacityEvidence:
    """严格读回证据包：未知键、缺失键、空样本、非有限数、内嵌密钥一律显式失败。"""
    mapping = _strict_object("evidence", payload, _EVIDENCE_KEYS)
    resources_raw = _require(mapping, "resources", "evidence")
    slo_raw = _require(mapping, "slo", "evidence")
    return CapacityEvidence(
        captured_at=_text("evidence.captured_at", _require(mapping, "captured_at", "evidence")),
        run_kind=_run_kind(_require(mapping, "run_kind", "evidence")),
        profile_hash=_digest("evidence.profile_hash", _require(mapping, "profile_hash", "evidence")),
        profile_file_digest=_digest(
            "evidence.profile_file_digest", _require(mapping, "profile_file_digest", "evidence")
        ),
        config_hash=_digest("evidence.config_hash", _require(mapping, "config_hash", "evidence")),
        workload=_parse_workload(_require(mapping, "workload", "evidence")),
        latencies=_parse_latencies(_require(mapping, "latencies", "evidence")),
        failure_counts=_parse_failure_counts(
            _require(mapping, "failure_counts", "evidence")
        ),
        execution=_parse_execution(_require(mapping, "execution", "evidence")),
        postgres=_parse_postgres(_require(mapping, "postgres", "evidence")),
        model=_parse_model(_require(mapping, "model", "evidence")),
        deployment=_parse_deployment(_require(mapping, "deployment", "evidence")),
        resources=None if resources_raw is None else _parse_resources(resources_raw),
        slo=None if slo_raw is None else _parse_slo(slo_raw),
        shared=_parse_shared(_require(mapping, "shared", "evidence")),
    )


def _parse_workload(value: object) -> WorkloadEvidence:
    path = "evidence.workload"
    mapping = _strict_object(
        path,
        value,
        (
            "users",
            "active_runs_per_user",
            "ramp_sec",
            "steady_sec",
            "turns_per_user",
            "effective_users",
            "attempts",
            "scale_users",
        ),
    )
    return WorkloadEvidence(
        users=_positive_int(f"{path}.users", _require(mapping, "users", path)),
        active_runs_per_user=_positive_int(
            f"{path}.active_runs_per_user", _require(mapping, "active_runs_per_user", path)
        ),
        ramp_sec=_non_negative_int(f"{path}.ramp_sec", _require(mapping, "ramp_sec", path)),
        steady_sec=_positive_int(f"{path}.steady_sec", _require(mapping, "steady_sec", path)),
        turns_per_user=_positive_int(
            f"{path}.turns_per_user", _require(mapping, "turns_per_user", path)
        ),
        effective_users=_positive_int(
            f"{path}.effective_users", _require(mapping, "effective_users", path)
        ),
        attempts=_positive_int(f"{path}.attempts", _require(mapping, "attempts", path)),
        scale_users=_optional_positive_int(
            f"{path}.scale_users", _require(mapping, "scale_users", path)
        ),
    )


def _parse_latencies(value: object) -> tuple[LatencyEvidence, ...]:
    path = "evidence.latencies"
    entries = _sequence(path, value)
    if not entries:
        raise EvidenceError(f"{path} 不能为空：没有样本就没有分位数")
    parsed: list[LatencyEvidence] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        item_path = f"{path}[{index}]"
        mapping = _strict_object(item_path, item, ("metric", "samples_ms"))
        metric = _text(f"{item_path}.metric", _require(mapping, "metric", item_path))
        if metric in seen:
            raise EvidenceError(f"{path} 的指标 {metric} 重复")
        seen.add(metric)
        samples = _sequence(f"{item_path}.samples_ms", _require(mapping, "samples_ms", item_path))
        if not samples:
            raise EvidenceError(f"{item_path} 没有样本：不给分位数（不伪造 0 延迟）")
        parsed.append(
            LatencyEvidence(
                metric=metric,
                samples_ms=tuple(
                    _finite_number(f"{item_path}.samples_ms[{position}]", sample)
                    for position, sample in enumerate(samples)
                ),
            )
        )
    return tuple(parsed)


def _parse_failure_counts(value: object) -> Mapping[str, int]:
    path = "evidence.failure_counts"
    allowed = tuple(kind.value for kind in FailureKind)
    mapping = _strict_subset_object(path, value, allowed)
    return {
        key: _non_negative_int(f"{path}.{key}", item)
        for key, item in sorted(mapping.items())
    }


def _parse_execution(value: object) -> ExecutionEvidence:
    path = "evidence.execution"
    mapping = _strict_object(
        path,
        value,
        (
            "slots_per_process",
            "measured_replicas",
            "peak_running_runs",
            "peak_waiting_runs",
            "peak_admitted_runs",
            "peak_active_streams",
            "sample_count",
            "wall_seconds",
        ),
    )
    slots = _unit_counts(
        f"{path}.slots_per_process",
        _require(mapping, "slots_per_process", path),
        units=AUTOSCALED_UNITS,
    )
    replicas = _unit_counts(
        f"{path}.measured_replicas", _require(mapping, "measured_replicas", path)
    )
    return ExecutionEvidence(
        slots_per_process=slots,
        measured_replicas=replicas,
        peak_running_runs=_non_negative_int(
            f"{path}.peak_running_runs", _require(mapping, "peak_running_runs", path)
        ),
        peak_waiting_runs=_non_negative_int(
            f"{path}.peak_waiting_runs", _require(mapping, "peak_waiting_runs", path)
        ),
        peak_admitted_runs=_non_negative_int(
            f"{path}.peak_admitted_runs", _require(mapping, "peak_admitted_runs", path)
        ),
        peak_active_streams=_non_negative_int(
            f"{path}.peak_active_streams", _require(mapping, "peak_active_streams", path)
        ),
        sample_count=_positive_int(f"{path}.sample_count", _require(mapping, "sample_count", path)),
        wall_seconds=_finite_number(
            f"{path}.wall_seconds", _require(mapping, "wall_seconds", path)
        ),
    )


def _parse_postgres(value: object) -> PostgresEvidence:
    path = "evidence.postgres"
    mapping = _strict_object(
        path,
        value,
        (
            "connections_peak",
            "connections_active_peak",
            "locks_waiting_peak",
            "wal_growth_bytes",
            "database_bytes",
        ),
    )
    return PostgresEvidence(
        connections_peak=_non_negative_int(
            f"{path}.connections_peak", _require(mapping, "connections_peak", path)
        ),
        connections_active_peak=_non_negative_int(
            f"{path}.connections_active_peak", _require(mapping, "connections_active_peak", path)
        ),
        locks_waiting_peak=_non_negative_int(
            f"{path}.locks_waiting_peak", _require(mapping, "locks_waiting_peak", path)
        ),
        wal_growth_bytes=_non_negative_int(
            f"{path}.wal_growth_bytes", _require(mapping, "wal_growth_bytes", path)
        ),
        database_bytes=_non_negative_int(
            f"{path}.database_bytes", _require(mapping, "database_bytes", path)
        ),
    )


def _parse_model(value: object) -> ModelBoundary:
    path = "evidence.model"
    mapping = _strict_object(
        path,
        value,
        (
            "mode",
            "credential_refs",
            "declared_max_inflight",
            "observed_calls",
            "observed_max_concurrency",
            "faults",
        ),
    )
    mode_raw = _text(f"{path}.mode", _require(mapping, "mode", path))
    try:
        mode = ModelMode(mode_raw)
    except ValueError as exc:
        raise EvidenceError(f"{path}.mode 必须是 CONTROLLED_PROBE 或 REAL") from exc
    refs = _sequence(f"{path}.credential_refs", _require(mapping, "credential_refs", path))
    faults = _string_keyed_object(f"{path}.faults", _require(mapping, "faults", path))
    return ModelBoundary(
        mode=mode,
        credential_refs=tuple(
            _env_reference(f"{path}.credential_refs[{index}]", item)
            for index, item in enumerate(refs)
        ),
        declared_max_inflight=_optional_positive_int(
            f"{path}.declared_max_inflight", _require(mapping, "declared_max_inflight", path)
        ),
        observed_calls=_non_negative_int(
            f"{path}.observed_calls", _require(mapping, "observed_calls", path)
        ),
        observed_max_concurrency=_non_negative_int(
            f"{path}.observed_max_concurrency", _require(mapping, "observed_max_concurrency", path)
        ),
        faults={key: _non_negative_int(f"{path}.faults.{key}", item) for key, item in faults.items()},
    )


def _parse_deployment(value: object) -> DeploymentFacts:
    path = "evidence.deployment"
    mapping = _strict_object(path, value, ("renderer", "units"))
    units_raw = _strict_object(f"{path}.units", _require(mapping, "units", path), UNITS)
    return DeploymentFacts(
        renderer=_text(f"{path}.renderer", _require(mapping, "renderer", path)),
        units={
            unit: _parse_unit_deployment(f"{path}.units.{unit}", units_raw[unit]) for unit in UNITS
        },
    )


def _parse_unit_deployment(path: str, value: object) -> UnitDeploymentFacts:
    item = _strict_object(
        path,
        value,
        (
            "replicas",
            "max_replicas",
            "cpu_request_millicores",
            "cpu_limit_millicores",
            "memory_request_mb",
            "memory_limit_mb",
            "termination_grace_sec",
            "execution_concurrency",
            "outbound_connections_per_process",
        ),
    )
    return UnitDeploymentFacts(
        replicas=_positive_int(f"{path}.replicas", _require(item, "replicas", path)),
        max_replicas=_positive_int(
            f"{path}.max_replicas", _require(item, "max_replicas", path)
        ),
        cpu_request_millicores=_positive_int(
            f"{path}.cpu_request_millicores", _require(item, "cpu_request_millicores", path)
        ),
        cpu_limit_millicores=_positive_int(
            f"{path}.cpu_limit_millicores", _require(item, "cpu_limit_millicores", path)
        ),
        memory_request_mb=_positive_int(
            f"{path}.memory_request_mb", _require(item, "memory_request_mb", path)
        ),
        memory_limit_mb=_positive_int(
            f"{path}.memory_limit_mb", _require(item, "memory_limit_mb", path)
        ),
        termination_grace_sec=_positive_int(
            f"{path}.termination_grace_sec", _require(item, "termination_grace_sec", path)
        ),
        execution_concurrency=_optional_positive_int(
            f"{path}.execution_concurrency", _require(item, "execution_concurrency", path)
        ),
        outbound_connections_per_process=_non_negative_int(
            f"{path}.outbound_connections_per_process",
            _require(item, "outbound_connections_per_process", path),
        ),
    )


def _parse_resources(value: object) -> ResourceEvidence:
    path = "evidence.resources"
    mapping = _strict_object(
        path,
        value,
        (
            "hardware",
            "process_counts",
            "replica_counts",
            "max_replica_counts",
            "pg_connection_budget",
            "fd_budget",
            "pg_connections_per_process",
            "storage_event_bytes_per_day",
            "storage_retention_days",
            "storage_artifact_bytes",
        ),
    )
    return ResourceEvidence(
        hardware=_parse_hardware_map(f"{path}.hardware", _require(mapping, "hardware", path)),
        process_counts=_unit_counts(
            f"{path}.process_counts", _require(mapping, "process_counts", path)
        ),
        replica_counts=_unit_counts(
            f"{path}.replica_counts", _require(mapping, "replica_counts", path)
        ),
        max_replica_counts=_unit_counts(
            f"{path}.max_replica_counts", _require(mapping, "max_replica_counts", path)
        ),
        pg_connection_budget=_positive_int(
            f"{path}.pg_connection_budget", _require(mapping, "pg_connection_budget", path)
        ),
        fd_budget=_positive_int(f"{path}.fd_budget", _require(mapping, "fd_budget", path)),
        pg_connections_per_process=_unit_counts(
            f"{path}.pg_connections_per_process",
            _require(mapping, "pg_connections_per_process", path),
            units=DB_UNITS,
        ),
        storage_event_bytes_per_day=_positive_int(
            f"{path}.storage_event_bytes_per_day",
            _require(mapping, "storage_event_bytes_per_day", path),
        ),
        storage_retention_days=_positive_int(
            f"{path}.storage_retention_days", _require(mapping, "storage_retention_days", path)
        ),
        storage_artifact_bytes=_non_negative_int(
            f"{path}.storage_artifact_bytes", _require(mapping, "storage_artifact_bytes", path)
        ),
    )


def _parse_hardware_map(path: str, value: object) -> dict[str, HardwareSpec]:
    mapping = _strict_object(path, value, UNITS)
    return {unit: _parse_hardware(f"{path}.{unit}", mapping[unit]) for unit in UNITS}


def _parse_hardware(path: str, value: object) -> HardwareSpec:
    mapping = _strict_object(
        path,
        value,
        (
            "cpu_request_millicores",
            "cpu_limit_millicores",
            "memory_request_mb",
            "memory_limit_mb",
        ),
    )
    request = _positive_int(
        f"{path}.cpu_request_millicores", _require(mapping, "cpu_request_millicores", path)
    )
    limit = _positive_int(
        f"{path}.cpu_limit_millicores", _require(mapping, "cpu_limit_millicores", path)
    )
    memory_request = _positive_int(
        f"{path}.memory_request_mb", _require(mapping, "memory_request_mb", path)
    )
    memory_limit = _positive_int(
        f"{path}.memory_limit_mb", _require(mapping, "memory_limit_mb", path)
    )
    if request > limit or memory_request > memory_limit:
        raise EvidenceError(f"{path} 的 request 不得超过 limit")
    return HardwareSpec(
        cpu_request_millicores=request,
        cpu_limit_millicores=limit,
        memory_request_mb=memory_request,
        memory_limit_mb=memory_limit,
    )


def _parse_slo(value: object) -> SloEvidence:
    path = "evidence.slo"
    mapping = _strict_object(
        path,
        value,
        (
            "admission_p95_ms",
            "admission_p99_ms",
            "framework_first_delta_p95_ms",
            "recovery_max_sec",
            "max_system_error_ratio",
        ),
    )
    ratio = _finite_number(
        f"{path}.max_system_error_ratio", _require(mapping, "max_system_error_ratio", path)
    )
    if ratio > 1.0:
        raise EvidenceError(f"{path}.max_system_error_ratio 必须 ≤ 1")
    return SloEvidence(
        admission_p95_ms=_positive_number(
            f"{path}.admission_p95_ms", _require(mapping, "admission_p95_ms", path)
        ),
        admission_p99_ms=_positive_number(
            f"{path}.admission_p99_ms", _require(mapping, "admission_p99_ms", path)
        ),
        framework_first_delta_p95_ms=_positive_number(
            f"{path}.framework_first_delta_p95_ms",
            _require(mapping, "framework_first_delta_p95_ms", path),
        ),
        recovery_max_sec=_positive_number(
            f"{path}.recovery_max_sec", _require(mapping, "recovery_max_sec", path)
        ),
        max_system_error_ratio=ratio,
    )


def _parse_shared(value: object) -> SharedBudgetFacts:
    path = "evidence.shared"
    mapping = _strict_object(
        path, value, ("redis_max_connections", "inbound_subscriber_limits", "sse_total_buffer_bytes")
    )
    limits = _unit_counts(
        f"{path}.inbound_subscriber_limits",
        _require(mapping, "inbound_subscriber_limits", path),
        units=("im-gateway", "agent-runtime"),
    )
    return SharedBudgetFacts(
        redis_max_connections=_positive_int(
            f"{path}.redis_max_connections", _require(mapping, "redis_max_connections", path)
        ),
        inbound_subscriber_limits=limits,
        sse_total_buffer_bytes=_positive_int(
            f"{path}.sse_total_buffer_bytes", _require(mapping, "sse_total_buffer_bytes", path)
        ),
    )


def write_evidence(path: str | Path, evidence: CapacityEvidence) -> Path:
    """把证据包写成 JSON 文件（utf-8、稳定键序）；IO 失败显式抛出。"""
    target = Path(path)
    try:
        target.write_text(
            json.dumps(evidence_payload(evidence), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise EvidenceError(f"证据包写入失败（{target}）：{exc.strerror or type(exc).__name__}") from exc
    return target


def load_evidence(path: str | Path) -> CapacityEvidence:
    """从 JSON 文件读回证据包并严格校验；坏 JSON/坏结构显式失败（不回落默认）。"""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceError(f"证据包读取失败（{source}）：{exc.strerror or type(exc).__name__}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"证据包不是合法 JSON（{source}，第 {exc.lineno} 行）") from exc
    return parse_evidence(payload)


# --------------------------------------------------------------------------------------
# 证据采集：真实负载报告 + 真实进程/清单/工厂事实 → 证据包
# --------------------------------------------------------------------------------------


def capture_evidence(
    *,
    load: LoadReport,
    profile: CapacityProfile,
    profile_file_digest: str,
    deployment: DeploymentFacts,
    measured_slots_per_process: Mapping[str, int],
    measured_replicas: Mapping[str, int],
    shared: SharedBudgetFacts,
    pg_connections_per_process: Mapping[str, int],
    model_declared_max_inflight: int | None,
    captured_at: str,
) -> CapacityEvidence:
    """从一次真实运行采集证据包；缺任何必需事实都显式失败（不补默认值）。"""
    if not load.samples:
        raise EvidenceError("负载报告没有并发采样：没有执行事实就没有容量证据")
    latencies = _latency_evidence(load)
    if not any(item.metric == "admission_ms" for item in latencies):
        raise EvidenceError("负载报告没有受理延迟样本：容量报告的最小证据缺失")
    slots = _require_measured_slots(measured_slots_per_process)
    return CapacityEvidence(
        captured_at=captured_at,
        run_kind=load.run_kind,
        profile_hash=load.locked_profile_hash,
        profile_file_digest=_digest("profile_file_digest", profile_file_digest),
        config_hash=config_hash(load.locked_profile_hash, deployment),
        workload=WorkloadEvidence(
            users=profile.workload.users,
            active_runs_per_user=profile.workload.active_runs_per_user,
            ramp_sec=profile.workload.ramp_sec,
            steady_sec=profile.workload.steady_sec,
            turns_per_user=profile.workload.turns_per_user,
            effective_users=load.effective_users,
            attempts=load.attempts(),
            scale_users=None if load.scale is None else load.scale.users,
        ),
        latencies=latencies,
        failure_counts=dict(load.failure_counts()),
        execution=_execution_evidence(load, slots=slots, replicas=measured_replicas),
        postgres=_postgres_evidence(load),
        model=_model_evidence(load, profile, model_declared_max_inflight),
        deployment=deployment,
        resources=(
            None
            if profile.resources is None
            else _resource_evidence(profile.resources, pg_connections_per_process)
        ),
        slo=None if profile.slo is None else _slo_evidence(profile),
        shared=shared,
    )


def _latency_evidence(load: LoadReport) -> tuple[LatencyEvidence, ...]:
    samples: dict[str, list[float]] = {}
    for turn in load.turns:
        if turn.admission_ms is not None:
            samples.setdefault("admission_ms", []).append(float(turn.admission_ms))
        if turn.first_delta_ms is not None:
            samples.setdefault("first_delta_ms", []).append(float(turn.first_delta_ms))
    return tuple(
        LatencyEvidence(metric=metric, samples_ms=tuple(values))
        for metric, values in sorted(samples.items())
    )


def _require_measured_slots(measured: Mapping[str, int]) -> dict[str, int]:
    missing = [unit for unit in AUTOSCALED_UNITS if unit not in measured]
    if missing:
        raise EvidenceError(f"缺真实执行槽证据（{', '.join(missing)}）：不能给单 Pod 容量")
    return {unit: int(measured[unit]) for unit in AUTOSCALED_UNITS}


def _execution_evidence(
    load: LoadReport, *, slots: Mapping[str, int], replicas: Mapping[str, int]
) -> ExecutionEvidence:
    return ExecutionEvidence(
        slots_per_process=dict(slots),
        measured_replicas={unit: int(replicas.get(unit, 0)) for unit in UNITS},
        peak_running_runs=max(sample.running_runs for sample in load.samples),
        peak_waiting_runs=max(sample.waiting_runs for sample in load.samples),
        peak_admitted_runs=max(sample.admitted_runs for sample in load.samples),
        peak_active_streams=max(sample.active_streams for sample in load.samples),
        sample_count=len(load.samples),
        wall_seconds=load.wall_seconds(),
    )


def _postgres_evidence(load: LoadReport) -> PostgresEvidence:
    before, after = load.postgres_before, load.postgres_after
    return PostgresEvidence(
        connections_peak=max(before.connections_total, after.connections_total),
        connections_active_peak=max(before.connections_active, after.connections_active),
        locks_waiting_peak=max(before.locks_waiting, after.locks_waiting),
        wal_growth_bytes=load.wal_growth_bytes,
        database_bytes=after.database_bytes,
    )


def _model_evidence(
    load: LoadReport, profile: CapacityProfile, declared_max_inflight: int | None
) -> ModelBoundary:
    probe = load.model_probe
    if probe is None:
        raise EvidenceError("负载报告没有模型探针回执：没有模型边界证据（不猜）")
    calls = probe.calls_after - probe.calls_before
    if calls < 0:
        raise EvidenceError("模型探针调用计数回退：证据不可信")
    return ModelBoundary(
        mode=ModelMode(profile.model.mode),
        credential_refs=tuple(
            entry.credential_ref for entry in profile.model.credentials_and_quotas
        ),
        declared_max_inflight=declared_max_inflight,
        observed_calls=calls,
        observed_max_concurrency=probe.max_concurrency,
        faults=dict(probe.faults),
    )


def _resource_evidence(
    resources: ResourceProfile, pg_connections_per_process: Mapping[str, int]
) -> ResourceEvidence:
    missing = [unit for unit in DB_UNITS if unit not in pg_connections_per_process]
    if missing:
        raise EvidenceError(f"缺每进程 PG 连接上限（{', '.join(missing)}）：无法核算总预算")
    return ResourceEvidence(
        hardware=dict(resources.hardware),
        process_counts=dict(resources.process_counts),
        replica_counts=dict(resources.replica_counts),
        max_replica_counts=dict(resources.max_replica_counts),
        pg_connection_budget=resources.pg_connection_budget,
        fd_budget=resources.fd_budget,
        pg_connections_per_process={
            unit: int(pg_connections_per_process[unit]) for unit in DB_UNITS
        },
        storage_event_bytes_per_day=resources.storage_budget.event_bytes_per_day,
        storage_retention_days=resources.storage_budget.retention_days,
        storage_artifact_bytes=resources.storage_budget.artifact_bytes,
    )


def _slo_evidence(profile: CapacityProfile) -> SloEvidence:
    slo = profile.slo
    if slo is None:  # pragma: no cover - 调用方已判定
        raise ReportError("profile 缺 slo：不产出 SLO 证据")
    return SloEvidence(
        admission_p95_ms=slo.admission_p95_ms,
        admission_p99_ms=slo.admission_p99_ms,
        framework_first_delta_p95_ms=slo.framework_first_delta_p95_ms,
        recovery_max_sec=slo.recovery_max_sec,
        max_system_error_ratio=slo.max_system_error_ratio,
    )


# --------------------------------------------------------------------------------------
# 部署事实：真实渲染清单（与 `kubectl apply -k` 同路径）
# --------------------------------------------------------------------------------------


def deployment_facts_from_manifests(base_dir: str | Path) -> DeploymentFacts:
    """从正式清单渲染结果取部署事实；缺单元/缺字段显式失败（不猜部署形状）。"""
    try:
        renderer, documents = preflight.render_manifests(Path(base_dir))
        views = preflight.build_units(documents, UNITS)
    except preflight.PreflightError as exc:
        raise ReportError(f"部署清单渲染失败：{exc}") from exc
    units = {view.name: _unit_deployment_facts(view) for view in views}
    missing = [unit for unit in UNITS if unit not in units]
    if missing:
        raise ReportError(f"渲染集合缺部署单元：{', '.join(missing)}")
    return DeploymentFacts(renderer=renderer, units=units)


def _unit_deployment_facts(view: preflight.DeploymentView) -> UnitDeploymentFacts:
    declared = view.container.get("resources")
    if not isinstance(declared, Mapping):
        raise ReportError(f"{view.name}: 清单缺 resources（requests/limits 必填）")
    requests, limits = declared.get("requests"), declared.get("limits")
    if not isinstance(requests, Mapping) or not isinstance(limits, Mapping):
        raise ReportError(f"{view.name}: 清单缺显式 requests/limits")
    grace = view.pod_spec.get("terminationGracePeriodSeconds")
    if not isinstance(grace, int) or isinstance(grace, bool) or grace < 1:
        raise ReportError(f"{view.name}: 清单必须显式声明 terminationGracePeriodSeconds")
    return UnitDeploymentFacts(
        replicas=view.replicas,
        max_replicas=view.max_replicas,
        cpu_request_millicores=_cpu_millicores(view.name, requests.get("cpu")),
        cpu_limit_millicores=_cpu_millicores(view.name, limits.get("cpu")),
        memory_request_mb=_memory_mebibytes(view.name, requests.get("memory")),
        memory_limit_mb=_memory_mebibytes(view.name, limits.get("memory")),
        termination_grace_sec=grace,
        execution_concurrency=_execution_concurrency(view),
        outbound_connections_per_process=_outbound_connections(view),
    )


def _execution_concurrency(view: preflight.DeploymentView) -> int | None:
    key = {
        "agent-runtime": "RUNTIME_EXECUTION_CONCURRENCY",
        "agent-worker": "WORKER_EXECUTION_CONCURRENCY",
    }.get(view.name)
    if key is None:
        return None
    item = view.env().get(key)
    if item is None or "value" not in item:
        raise ReportError(f"{view.name}: 清单缺执行槽字面量 {key}（启动固定值必须显式）")
    return _literal_int(f"{view.name}.{key}", item["value"])


def _outbound_connections(view: preflight.DeploymentView) -> int:
    total = 0
    for key, item in view.env().items():
        if _POOL_KEY.fullmatch(key) is None or "value" not in item:
            continue
        total += _literal_int(f"{view.name}.{key}", item["value"])
    return total


def _literal_int(label: str, value: object) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise ReportError(f"{label} 必须是字面整数，收到 {value!r}")
    return int(value)


def _cpu_millicores(unit: str, value: object) -> int:
    if not isinstance(value, str):
        raise ReportError(f"{unit}: CPU 数量必须是字符串，收到 {value!r}")
    match = _CPU_QUANTITY.fullmatch(value)
    if match is None:
        raise ReportError(f"{unit}: CPU 数量 {value!r} 不是合法数量")
    if match.group("millicores") is not None:
        return int(match.group("millicores"))
    return round(float(match.group("cores")) * 1000)


def _memory_mebibytes(unit: str, value: object) -> int:
    if not isinstance(value, str):
        raise ReportError(f"{unit}: 内存数量必须是字符串，收到 {value!r}")
    match = _MEMORY_QUANTITY.fullmatch(value)
    if match is None:
        raise ReportError(f"{unit}: 内存数量 {value!r} 必须带 Mi/Gi 后缀")
    factor = 1 if match.group("suffix") == "Mi" else 1024
    return int(match.group("value")) * factor


# --------------------------------------------------------------------------------------
# 报告生成：分位数 / SLO / 错误账目 / 瓶颈 / 资源预算 / 副本公式
# --------------------------------------------------------------------------------------


def build_report(
    evidence: CapacityEvidence,
    *,
    pg_maintenance_reserve: int,
    replica_targets: Sequence[ReplicaTarget] = (),
) -> CapacityReport:
    """从证据包生成报告；所有数字来自证据，缺证据显式失败或只给诊断结论。"""
    if pg_maintenance_reserve < 0:
        raise ReportError(f"PG 维护保留量必须 ≥ 0，收到 {pg_maintenance_reserve}")
    _verify_config_hash(evidence)
    latencies = _latency_facts(evidence)
    error_counts, error_ratio = _error_accounting(evidence)
    slo_results = _slo_results(evidence, latencies, error_ratio)
    resources = _resource_lines(evidence, pg_maintenance_reserve)
    bottlenecks = _bottlenecks(evidence, pg_maintenance_reserve)
    replicas = recommend_replicas(evidence, replica_targets)
    blockers = _blockers(evidence, latencies, resources, replicas)
    verdict = _verdict(slo_results, resources, bottlenecks, blockers)
    return CapacityReport(
        captured_at=evidence.captured_at,
        config_hash=evidence.config_hash,
        profile_hash=evidence.profile_hash,
        run_kind=evidence.run_kind,
        verdict=verdict,
        latencies=latencies,
        slo=slo_results,
        error_counts=error_counts,
        error_ratio=error_ratio,
        bottlenecks=bottlenecks,
        resources=resources,
        replicas=replicas,
        model_boundary=evidence.model,
        blockers=blockers,
        notes=_notes(evidence, replicas),
    )


def _verify_config_hash(evidence: CapacityEvidence) -> None:
    expected = config_hash(evidence.profile_hash, evidence.deployment)
    if evidence.config_hash != expected:
        raise EvidenceError(
            "证据包的 config_hash 与记录的 profile/部署事实不一致：证据被改动或混用了两份配置"
        )


def _latency_facts(evidence: CapacityEvidence) -> tuple[PercentileFact, ...]:
    facts: list[PercentileFact] = []
    for item in evidence.latencies:
        try:
            summary = summarize_latencies(item.samples_ms)
        except MetricsError as exc:
            raise EvidenceError(f"{item.metric} 的样本不可用：{exc}") from exc
        facts.append(
            PercentileFact(
                metric=item.metric,
                count=summary.count,
                p50_ms=summary.p50,
                p95_ms=summary.p95,
                p99_ms=summary.p99,
                max_ms=summary.maximum,
            )
        )
    present = {fact.metric for fact in facts}
    missing = [metric for metric in REQUIRED_METRICS if metric not in present]
    if missing:
        raise EvidenceError(f"证据包缺必需延迟样本（{', '.join(missing)}）：不能给容量结论")
    return tuple(facts)


def _error_accounting(evidence: CapacityEvidence) -> tuple[Mapping[str, int], float]:
    unknown = sorted(set(evidence.failure_counts) - SYSTEM_ERROR_KINDS - OVERLOAD_KINDS)
    if unknown:
        raise EvidenceError(f"证据包含未分类的失败类别：{', '.join(unknown)}")
    system = sum(count for kind, count in evidence.failure_counts.items() if kind in SYSTEM_ERROR_KINDS)
    overload = sum(count for kind, count in evidence.failure_counts.items() if kind in OVERLOAD_KINDS)
    attempts = evidence.workload.attempts
    if attempts < 1:
        raise EvidenceError("证据包的尝试数为 0：没有负载就没有错误率")
    counts = {"system": system, "overload": overload, "total": system + overload}
    return counts, system / attempts


def _slo_results(
    evidence: CapacityEvidence,
    latencies: Sequence[PercentileFact],
    error_ratio: float,
) -> tuple[SloResult, ...]:
    slo = evidence.slo
    if slo is None:
        return ()
    by_metric = {fact.metric: fact for fact in latencies}
    results: list[SloResult] = []
    admission = by_metric.get("admission_ms")
    if admission is not None:
        results.append(
            SloResult("admission_p95_ms", admission.p95_ms, slo.admission_p95_ms,
                      admission.p95_ms <= slo.admission_p95_ms)
        )
        results.append(
            SloResult("admission_p99_ms", admission.p99_ms, slo.admission_p99_ms,
                      admission.p99_ms <= slo.admission_p99_ms)
        )
    first_delta = by_metric.get("first_delta_ms")
    if first_delta is not None:
        results.append(
            SloResult(
                "framework_first_delta_p95_ms",
                first_delta.p95_ms,
                slo.framework_first_delta_p95_ms,
                first_delta.p95_ms <= slo.framework_first_delta_p95_ms,
            )
        )
    results.append(
        SloResult(
            "system_error_ratio", error_ratio, slo.max_system_error_ratio,
            error_ratio <= slo.max_system_error_ratio,
        )
    )
    return tuple(results)


def _bottlenecks(evidence: CapacityEvidence, pg_maintenance_reserve: int) -> tuple[Bottleneck, ...]:
    """只产出本次运行真实触到的上限：观测值/上限都来自证据。"""
    items: list[Bottleneck] = []
    slots = _slots_bottleneck(evidence)
    if slots is not None:
        items.append(slots)
    model = evidence.model
    if (
        model.declared_max_inflight is not None
        and model.observed_max_concurrency >= model.declared_max_inflight
    ):
        items.append(
            Bottleneck(
                kind=BottleneckKind.MODEL_INFLIGHT,
                unit=None,
                observed=float(model.observed_max_concurrency),
                ceiling=float(model.declared_max_inflight),
                exhausted=True,
                detail=(
                    f"模型在飞上限被触到：探针观测最大并发 {model.observed_max_concurrency} ≥ "
                    f"声明额度 {model.declared_max_inflight}（边界模式 {model.mode.value}）"
                ),
            )
        )
    if evidence.execution.peak_waiting_runs > 0:
        ceiling = float(evidence.workload.effective_users)
        waiting = float(evidence.execution.peak_waiting_runs)
        items.append(
            Bottleneck(
                kind=BottleneckKind.ADMISSION_QUEUE,
                unit="agent-runtime",
                observed=waiting,
                ceiling=ceiling,
                exhausted=waiting + evidence.execution.peak_running_runs >= ceiling,
                detail=f"队列真实出现等待：峰值等待 {int(waiting)}（目标并发 {int(ceiling)}）",
            )
        )
    pg = _pg_bottleneck(evidence, pg_maintenance_reserve)
    if pg is not None:
        items.append(pg)
    return tuple(items)


def _slots_bottleneck(evidence: CapacityEvidence) -> Bottleneck | None:
    resources = evidence.resources
    execution = evidence.execution
    slots = execution.slots_per_process.get("agent-runtime")
    replicas = execution.measured_replicas.get("agent-runtime", 0)
    if resources is None or slots is None or replicas < 1:
        return None
    ceiling = float(slots * resources.process_counts["agent-runtime"] * replicas)
    observed = float(execution.peak_running_runs)
    if observed < ceiling:
        return None
    return Bottleneck(
        kind=BottleneckKind.EXECUTION_SLOTS,
        unit="agent-runtime",
        observed=observed,
        ceiling=ceiling,
        exhausted=True,
        detail=(
            f"Runtime 执行槽压满：峰值在飞 Run {int(observed)} ≥ {slots} 槽 × "
            f"{resources.process_counts['agent-runtime']} 进程 × {replicas} 副本"
        ),
    )


def _pg_bottleneck(evidence: CapacityEvidence, reserve: int) -> Bottleneck | None:
    resources = evidence.resources
    if resources is None:
        return None
    total = evidence.postgres.connections_peak + reserve
    if total <= resources.pg_connection_budget:
        return None
    return Bottleneck(
        kind=BottleneckKind.PG_CONNECTIONS,
        unit=None,
        observed=float(total),
        ceiling=float(resources.pg_connection_budget),
        exhausted=True,
        detail=(
            f"PG 连接峰值 {evidence.postgres.connections_peak} + 维护保留 {reserve} 超过预算 "
            f"{resources.pg_connection_budget}"
        ),
    )


def _resource_lines(
    evidence: CapacityEvidence, pg_maintenance_reserve: int
) -> tuple[ResourceBudgetLine, ...]:
    resources = evidence.resources
    if resources is None:
        return ()
    pg_total = _pg_total(resources)
    pg_ok = pg_total + pg_maintenance_reserve <= resources.pg_connection_budget
    lines: list[ResourceBudgetLine] = []
    for unit in UNITS:
        facts = evidence.deployment.units.get(unit)
        if facts is None:
            raise ReportError(f"部署事实缺 {unit}：资源预算无法核算")
        per_process = resources.pg_connections_per_process.get(unit, 0) if unit in DB_UNITS else 0
        pg_share = resources.process_counts[unit] * resources.max_replica_counts[unit] * per_process
        fd = (
            facts.outbound_connections_per_process
            + per_process
            + evidence.shared.redis_max_connections
            + evidence.shared.inbound_subscriber_limits.get(unit, 0)
        )
        fd_ok = fd <= resources.fd_budget
        lines.append(
            ResourceBudgetLine(
                unit=unit,
                cpu_request_millicores=facts.cpu_request_millicores,
                cpu_limit_millicores=facts.cpu_limit_millicores,
                memory_request_mb=facts.memory_request_mb,
                memory_limit_mb=facts.memory_limit_mb,
                pg_connections_at_max_replicas=pg_share,
                fd_per_process=fd,
                within_pg_budget=pg_ok,
                within_fd_budget=fd_ok,
                within_budget=pg_ok and fd_ok,
                detail=(
                    f"PG 总连接（进程×最大副本）{pg_total} + 维护保留 {pg_maintenance_reserve} "
                    f"{'≤' if pg_ok else '>'} 预算 {resources.pg_connection_budget}；每进程 FD {fd} "
                    f"{'≤' if fd_ok else '>'} fd_budget {resources.fd_budget}"
                ),
            )
        )
    return tuple(lines)


def _pg_total(resources: ResourceEvidence) -> int:
    return sum(
        resources.process_counts[unit]
        * resources.max_replica_counts[unit]
        * resources.pg_connections_per_process[unit]
        for unit in DB_UNITS
    )


def recommend_replicas(
    evidence: CapacityEvidence, targets: Sequence[ReplicaTarget]
) -> tuple[ReplicaRecommendation, ...]:
    """按 `N = ceil(目标并发 / 单 Pod 容量 / 目标利用率) + 冗余副本` 给出副本建议。"""
    return tuple(_recommend(evidence, target) for target in targets)


def _recommend(evidence: CapacityEvidence, target: ReplicaTarget) -> ReplicaRecommendation:
    resources = evidence.resources
    if resources is None:
        raise EvidenceError("证据包缺资源声明（process_counts）：不能给单 Pod 容量")
    slots = evidence.execution.slots_per_process.get(target.unit)
    if slots is None or slots < 1:
        raise EvidenceError(f"证据包缺 {target.unit} 的真实执行槽：不能给出 Pod 数（不虚构）")
    if target.unit == "agent-runtime" and evidence.execution.peak_running_runs < 1:
        raise EvidenceError("本次运行没有观测到真实执行中的 Run：不能给出 Runtime 副本建议")
    per_pod = slots * resources.process_counts[target.unit]
    raw = target.target_execution_concurrency / per_pod / target.target_utilization
    recommended = max(1, math.ceil(raw) + target.redundancy_replicas)
    facts = evidence.deployment.units.get(target.unit)
    if facts is None:
        raise ReportError(f"部署事实缺 {target.unit}：副本建议无法核对上界")
    replicas = evidence.execution.measured_replicas.get(target.unit, 0)
    observed_per_pod = (
        evidence.execution.peak_running_runs / replicas
        if target.unit == "agent-runtime" and replicas > 0
        else 0.0
    )
    basis = (
        CAPACITY_BASIS_MEASURED
        if observed_per_pod >= per_pod
        else CAPACITY_BASIS_DECLARED
    )
    return ReplicaRecommendation(
        unit=target.unit,
        target_execution_concurrency=target.target_execution_concurrency,
        per_pod_capacity=per_pod,
        target_utilization=target.target_utilization,
        redundancy_replicas=target.redundancy_replicas,
        recommended_replicas=recommended,
        max_replicas=facts.max_replicas,
        within_max_replicas=recommended <= facts.max_replicas,
        capacity_basis=basis,
        formula=(
            f"ceil({target.target_execution_concurrency} / {per_pod} / "
            f"{target.target_utilization:g}) + {target.redundancy_replicas} = {recommended}"
        ),
    )


def _blockers(
    evidence: CapacityEvidence,
    latencies: Sequence[PercentileFact],
    resources: Sequence[ResourceBudgetLine],
    replicas: Sequence[ReplicaRecommendation],
) -> tuple[str, ...]:
    """生产达标的前置条件与真实不一致项：缺任一项就不给生产结论。"""
    blockers: list[str] = []
    if (
        evidence.run_kind is not RunKind.ACCEPTANCE
        or evidence.workload.scale_users is not None
        or evidence.workload.effective_users != evidence.workload.users
    ):
        blockers.append(
            f"受限规模运行（有效用户 {evidence.workload.effective_users}/"
            f"{evidence.workload.users}，scale={evidence.workload.scale_users}）："
            "不冒充全量验收结论"
        )
    if evidence.slo is None:
        blockers.append("证据包缺 SLO 阈值：不能给出生产达标结论（只能诊断）")
    if evidence.resources is None:
        blockers.append("证据包缺资源声明（requests/limits、连接/FD/存储预算）：不能给出生产结论")
    if not any(fact.metric == "first_delta_ms" for fact in latencies):
        blockers.append("证据包缺首字延迟样本：framework_first_delta_p95 无法判定")
    blockers.extend(_drift_blockers(evidence))
    over = [item for item in replicas if not item.within_max_replicas]
    for item in over:
        blockers.append(
            f"{item.unit} 的副本建议 {item.recommended_replicas} 超过部署上界 "
            f"{item.max_replicas}：需调整 HPA 上界与 PG/下游预算后复测"
        )
    return tuple(blockers)


def _drift_blockers(evidence: CapacityEvidence) -> list[str]:
    """测量配置与生产部署事实不一致时必须显式列出（容量数字不能张冠李戴）。"""
    resources = evidence.resources
    if resources is None:
        return []
    blockers: list[str] = []
    for unit in UNITS:
        facts = evidence.deployment.units.get(unit)
        if facts is None:
            blockers.append(f"部署事实缺 {unit}：无法核对测量配置与生产清单")
            continue
        hardware = resources.hardware[unit]
        if (
            facts.cpu_request_millicores,
            facts.cpu_limit_millicores,
            facts.memory_request_mb,
            facts.memory_limit_mb,
        ) != (
            hardware.cpu_request_millicores,
            hardware.cpu_limit_millicores,
            hardware.memory_request_mb,
            hardware.memory_limit_mb,
        ):
            blockers.append(f"{unit}: 渲染清单 requests/limits 与容量 profile 漂移")
        if facts.max_replicas != resources.max_replica_counts[unit]:
            blockers.append(
                f"{unit}: 生效副本上界 {facts.max_replicas} ≠ profile "
                f"max_replica_counts={resources.max_replica_counts[unit]}"
            )
        measured = evidence.execution.slots_per_process.get(unit)
        if measured is not None and facts.execution_concurrency != measured:
            blockers.append(
                f"{unit}: 测量执行槽 {measured} ≠ 生产清单 {facts.execution_concurrency}"
            )
    return blockers


def _verdict(
    slo_results: Sequence[SloResult],
    resources: Sequence[ResourceBudgetLine],
    bottlenecks: Sequence[Bottleneck],
    blockers: Sequence[str],
) -> Verdict:
    """SLO 违约、预算超限或 PG 连接实测越界 ⇒ 不达标；证据不足 ⇒ 只能诊断。"""
    if any(not result.passed for result in slo_results):
        return Verdict.REJECTED
    if any(not line.within_budget for line in resources):
        return Verdict.REJECTED
    if any(item.kind is BottleneckKind.PG_CONNECTIONS for item in bottlenecks):
        return Verdict.REJECTED
    if blockers:
        return Verdict.DIAGNOSTIC
    return Verdict.ACCEPTED


def _notes(
    evidence: CapacityEvidence, replicas: Sequence[ReplicaRecommendation]
) -> tuple[str, ...]:
    notes: list[str] = []
    if evidence.model.mode is ModelMode.CONTROLLED_PROBE:
        notes.append(
            "模型边界 = 受控 HTTP 探针：探针吞吐不冒充真实模型吞吐，真实供方额度未验证"
        )
    else:
        refs = ", ".join(evidence.model.credential_refs) or "（未声明）"
        notes.append(f"模型边界 = 真实模型；供方额度证据引用 {refs}（本报告不解析密钥值）")
    if not replicas:
        notes.append("未给出业务确认的副本建议参数（目标并发/利用率/冗余）：本次不产出 Pod 数")
    declared = [item.unit for item in replicas if item.capacity_basis == CAPACITY_BASIS_DECLARED]
    if declared:
        notes.append(
            f"以下单元的执行槽未被压满，单 Pod 容量按真实声明配置计（未实测压满）：{', '.join(declared)}"
        )
    if evidence.resources is not None:
        notes.append(
            "存储预算：事件 "
            f"{evidence.resources.storage_event_bytes_per_day} 字节/天 × 保留 "
            f"{evidence.resources.storage_retention_days} 天 + 制品 "
            f"{evidence.resources.storage_artifact_bytes} 字节（磁盘另计索引/WAL/备份）"
        )
    notes.append(
        f"真实观测：WAL 增长 {evidence.postgres.wal_growth_bytes} 字节、库大小 "
        f"{evidence.postgres.database_bytes} 字节、并发采样 {evidence.execution.sample_count} 次"
    )
    return tuple(notes)


# --------------------------------------------------------------------------------------
# 渲染 / CLI
# --------------------------------------------------------------------------------------


def render_markdown(report: CapacityReport) -> str:
    """把报告渲染成可归档的 Markdown（运营文档引用的数字都在这里）。"""
    lines = [
        "# Agent 容量报告（Runtime/Worker 副本建议）",
        "",
        f"- 结论：**{report.verdict.value}**",
        f"- 配置 hash：`{report.config_hash}`（profile `{report.profile_hash}`）",
        f"- 运行类型：{report.run_kind.value}；采集时间：{report.captured_at}",
        f"- 模型边界：{report.model_boundary.mode.value}"
        f"（真实供方容量证据：{report.model_boundary.real_supplier_capacity}）",
        "",
    ]
    lines.extend(_markdown_latencies(report))
    lines.extend(_markdown_slo(report))
    lines.extend(_markdown_bottlenecks(report))
    lines.extend(_markdown_resources(report))
    lines.extend(_markdown_replicas(report))
    if report.blockers:
        lines.append("## 阻塞项（不给生产结论的理由）")
        lines.append("")
        lines.extend(f"- {blocker}" for blocker in report.blockers)
        lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.extend(f"- {note}" for note in report.notes)
    return "\n".join(lines) + "\n"


def _markdown_latencies(report: CapacityReport) -> list[str]:
    lines = [
        "## 延迟分位数（最近秩，来自真实样本）",
        "",
        "| 指标 | 样本数 | P50 | P95 | P99 | max |",
        "|---|---|---|---|---|---|",
    ]
    for fact in report.latencies:
        lines.append(
            f"| {fact.metric} | {fact.count} | {fact.p50_ms:.1f} | {fact.p95_ms:.1f} | "
            f"{fact.p99_ms:.1f} | {fact.max_ms:.1f} |"
        )
    lines.append("")
    return lines


def _markdown_slo(report: CapacityReport) -> list[str]:
    lines = [
        "## SLO 判定",
        "",
        f"错误账目：系统 {report.error_counts['system']} / 过载 {report.error_counts['overload']} / "
        f"总 {report.error_counts['total']}；系统错误率 {report.error_ratio:.4f}",
        "",
        "| 指标 | 观测 | 阈值 | 结论 |",
        "|---|---|---|---|",
    ]
    for result in report.slo:
        lines.append(
            f"| {result.metric} | {result.observed:.2f} | {result.threshold:.2f} | "
            f"{'通过' if result.passed else '不达标'} |"
        )
    lines.append("")
    return lines


def _markdown_bottlenecks(report: CapacityReport) -> list[str]:
    lines = [
        "## 真实触到的瓶颈",
        "",
        "| 类别 | 单元 | 观测 | 上限 | 压满 | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    for item in report.bottlenecks:
        lines.append(
            f"| {item.kind.value} | {item.unit or '-'} | {item.observed:g} | {item.ceiling:g} | "
            f"{'是' if item.exhausted else '否'} | {item.detail} |"
        )
    if not report.bottlenecks:
        lines.append("| （无） | - | - | - | - | 本次运行未触到任何上限 |")
    lines.append("")
    return lines


def _markdown_resources(report: CapacityReport) -> list[str]:
    lines = [
        "## 资源与连接预算",
        "",
        "| 单元 | CPU req/limit | 内存 req/limit | PG 连接（最大副本） | 每进程 FD | 预算内 |",
        "|---|---|---|---|---|---|",
    ]
    for line in report.resources:
        lines.append(
            f"| {line.unit} | {line.cpu_request_millicores}m/{line.cpu_limit_millicores}m | "
            f"{line.memory_request_mb}Mi/{line.memory_limit_mb}Mi | "
            f"{line.pg_connections_at_max_replicas} | {line.fd_per_process} | "
            f"{'是' if line.within_budget else '否'} |"
        )
    lines.append("")
    return lines


def _markdown_replicas(report: CapacityReport) -> list[str]:
    lines = [
        "## Runtime/Worker 副本建议",
        "",
        "公式：`N = ceil(目标执行并发 / 单 Pod 容量 / 目标利用率) + 冗余副本`",
        "",
        "| 单元 | 目标并发 | 单 Pod 容量 | 利用率 | 冗余 | 建议副本 | 上界 | 容量口径 | 算式 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in report.replicas:
        lines.append(
            f"| {item.unit} | {item.target_execution_concurrency} | {item.per_pod_capacity} | "
            f"{item.target_utilization:g} | {item.redundancy_replicas} | "
            f"{item.recommended_replicas} | {item.max_replicas} | {item.capacity_basis} | "
            f"`{item.formula}` |"
        )
    if not report.replicas:
        lines.append("| （无） | - | - | - | - | - | - | - | 未提供业务参数 |")
    lines.append("")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：从证据包渲染报告（0 成功；2 证据/配置不可用）。"""
    parser = argparse.ArgumentParser(description="从容量证据包生成报告（TASK-117）")
    parser.add_argument("--evidence", type=Path, required=True, help="证据包 JSON 路径")
    parser.add_argument("--pg-maintenance-reserve", type=int, required=True, help="PG 维护保留连接")
    parser.add_argument("--runtime-target", type=int, help="Runtime 目标执行并发（业务确认）")
    parser.add_argument("--worker-target", type=int, help="Worker 目标执行并发（业务确认）")
    parser.add_argument("--utilization", type=float, help="目标利用率 (0,1]（业务确认）")
    parser.add_argument("--redundancy", type=int, help="冗余副本（业务确认）")
    args = parser.parse_args(argv)
    try:
        evidence = load_evidence(args.evidence)
        targets = _cli_targets(args)
        report = build_report(
            evidence,
            pg_maintenance_reserve=args.pg_maintenance_reserve,
            replica_targets=targets,
        )
    except ReportError as exc:
        print(f"报告无法生成：{exc}", file=sys.stderr)
        return 2
    print(render_markdown(report), end="")
    return 0


def _cli_targets(args: argparse.Namespace) -> tuple[ReplicaTarget, ...]:
    requested = {"agent-runtime": args.runtime_target, "agent-worker": args.worker_target}
    if any(value is None for value in requested.values()):
        if any(value is not None for value in requested.values()):
            raise ReportError("--runtime-target/--worker-target 必须同时给出（或都不给）")
        return ()
    if args.utilization is None or args.redundancy is None:
        raise ReportError("给出目标并发时必须同时给出 --utilization 与 --redundancy")
    return tuple(
        ReplicaTarget(
            unit=unit,
            target_execution_concurrency=int(value),
            target_utilization=args.utilization,
            redundancy_replicas=args.redundancy,
        )
        for unit, value in requested.items()
        if value is not None
    )


# --------------------------------------------------------------------------------------
# 严格校验原语（不回落默认、错误消息不回带原始内部内容）
# --------------------------------------------------------------------------------------


def _strict_object(path: str, value: object, allowed: Sequence[str]) -> Mapping[str, object]:
    mapping = _string_keyed_object(path, value)
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise EvidenceError(f"{path} 含未知键：{', '.join(unknown)}")
    missing = [key for key in allowed if key not in mapping]
    if missing:
        raise EvidenceError(f"{path} 缺必填键：{', '.join(missing)}")
    return mapping


def _strict_subset_object(path: str, value: object, allowed: Sequence[str]) -> Mapping[str, object]:
    """只允许 `allowed` 里的键，但不要求全部出现（失败账目只列观测到的类别）。"""
    mapping = _string_keyed_object(path, value)
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise EvidenceError(f"{path} 含未知键：{', '.join(unknown)}")
    return mapping


def _string_keyed_object(path: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EvidenceError(f"{path} 必须是对象，收到 {type(value).__name__}")
    mapping = dict(value)
    for key in mapping:
        if not isinstance(key, str):
            raise EvidenceError(f"{path} 的键必须是字符串（不回带键原文）")
    return mapping


def _require(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        raise EvidenceError(f"{path} 缺必填键 {key}")
    return mapping[key]


def _sequence(path: str, value: object) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EvidenceError(f"{path} 必须是列表")
    return value


def _text(path: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{path} 必须是非空字符串")
    return value


def _run_kind(value: object) -> RunKind:
    text = _text("evidence.run_kind", value)
    try:
        return RunKind(text)
    except ValueError as exc:
        raise EvidenceError("evidence.run_kind 必须是 ACCEPTANCE 或 VERIFICATION") from exc


def _digest(path: str, value: object) -> str:
    text = _text(path, value)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise EvidenceError(f"{path} 必须是 sha256 十六进制摘要")
    return text


def _env_reference(path: str, value: object) -> str:
    text = _text(path, value)
    if _ENV_REFERENCE.fullmatch(text) is None:
        raise EvidenceError(f"{path} 必须是 env: 引用（不得内嵌密钥原文）")
    return text


def _positive_int(path: str, value: object) -> int:
    number = _integer(path, value)
    if number < 1:
        raise EvidenceError(f"{path} 必须 ≥ 1")
    return number


def _non_negative_int(path: str, value: object) -> int:
    number = _integer(path, value)
    if number < 0:
        raise EvidenceError(f"{path} 必须 ≥ 0")
    return number


def _integer(path: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(f"{path} 必须是整数")
    return value


def _optional_positive_int(path: str, value: object) -> int | None:
    if value is None:
        return None
    return _positive_int(path, value)


def _finite_number(path: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"{path} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise EvidenceError(f"{path} 必须是有限非负数")
    return number


def _positive_number(path: str, value: object) -> float:
    number = _finite_number(path, value)
    if number <= 0.0:
        raise EvidenceError(f"{path} 必须 > 0")
    return number


def _unit_counts(
    path: str, value: object, *, units: Sequence[str] = UNITS
) -> dict[str, int]:
    mapping = _strict_object(path, value, units)
    return {unit: _positive_int(f"{path}.{unit}", mapping[unit]) for unit in units}


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
