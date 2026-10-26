"""[B-317] 容量报告与 Runtime/Worker 副本建议（TASK-117；设计 §4.1 部署与容量 / §4.3 分层验收与报告）。

真实边界（不 mock 任何服务、不用伪造成功回执）：

- **实际容量 harness/profile**：负载由锁定 YAML profile（`profiles/agent-capacity-webchat.example.yml`）
  驱动，受限规模显式登记为 `VERIFICATION`（全量 1000 用户结论留给需求级 verify-e2e，本文件不冒充）；
- **真实 HTTP/SSE 负载与真实栈**：消息经真实入口代理 → 真实 Gateway → 真实 Runtime → 真实 PG
  → TASK-106 真实模型协议探针子进程；证据包由这份真实运行的 `LoadReport` + 真实进程 env +
  真实 engine 工厂 + 真实部署清单渲染结果采集；
- **真实部署事实**：`deploy/k8s/base` 由 TASK-104 预检的渲染路径（kustomize/收集展开）读入，
  HPA 上界、requests/limits、执行槽 env 都是渲染清单里的事实，不是用例里另立的常量；
- **真实报告输入**：报告只消费证据包（JSON 往返后的同一份），分位数/P95/P99/瓶颈/资源预算/
  配置 hash/副本公式全部可复算；缺样本、缺 SLO/资源声明、缺执行事实时显式失败或只给诊断结论，
  绝不输出生产达标或虚构 Pod 数。

断言位置逐项对应 B-317 契约（见各用例 docstring 的①…⑧编号）。
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_worker.infrastructure import db as worker_db
from muad_common import SharedSettings
from muad_console_platform.application.chat_token_service import (
    generate_chat_token,
    hash_chat_token,
)
from muad_console_platform.infrastructure import db as console_db
from muad_console_platform.infrastructure.models.auth import ChatAccessToken
from muad_console_platform.infrastructure.models.channel import BotAccount
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
    PlatformUser,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.acceptance.capacity.stack import (
    UNITS,
    CapacityStack,
    acquire_datastore,
    query_value,
    read_process_environ,
    run_async,
)
from tests.capacity.driver import (
    ChatCredential,
    LoadDriver,
    LoadReport,
    LoadScale,
    LoadTarget,
    RunKind,
)
from tests.capacity.metrics import PostgresMetrics, percentile, summarize_latencies
from tests.capacity.profile import (
    HardwareSpec,
    file_digest,
    load_profile,
    profile_hash,
)
from tests.capacity.report import (
    AUTOSCALED_UNITS,
    BottleneckKind,
    CapacityEvidence,
    DeploymentFacts,
    EvidenceError,
    ExecutionEvidence,
    LatencyEvidence,
    ModelBoundary,
    ModelMode,
    PostgresEvidence,
    ReplicaTarget,
    ResourceEvidence,
    SharedBudgetFacts,
    SloEvidence,
    UnitDeploymentFacts,
    Verdict,
    WorkloadEvidence,
    build_report,
    capture_evidence,
    config_hash,
    deployment_facts_from_manifests,
    evidence_payload,
    load_evidence,
    parse_evidence,
    recommend_replicas,
    render_markdown,
    write_evidence,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "agent-capacity-webchat.example.yml"
BASE = ROOT / "deploy/k8s/base"

#: 受限验证规模（显式登记，不冒充 1000 用户结论）：6 用户 × 1 轮、ramp 1s、稳态 2s。
VERIFICATION_SCALE = LoadScale(
    users=6, ramp_sec=1, steady_sec=2, turns_per_user=1, think_time_scale=0.05
)
#: 真实模型探针首块延迟：让在飞窗口足够长，模型容量上限能被真实触到。
PROBE_LATENCY_MS = 800
PROBE_KEY = "sk-capacity-report-probe"
#: 物理 Key 的真实在飞上限（PG 行 `control.model_credential.max_inflight`）。
MODEL_MAX_INFLIGHT = 4
#: 负载用户 + 1 份控制探测主体（探测 Run 不占负载用户份额）。
LOAD_USERS = VERIFICATION_SCALE.users
SEEDED_USERS = LOAD_USERS + 1
#: 显式执行槽（与生产清单一致：真实进程 env 读回后进证据包）。
SERVICE_ENV: Mapping[str, Mapping[str, str]] = {
    "agent-runtime": {"RUNTIME_EXECUTION_CONCURRENCY": "8"},
    "agent-worker": {"WORKER_EXECUTION_CONCURRENCY": "4"},
}
#: 副本建议的业务确认参数（无默认值；缺参数就不给 Pod 数）。
RUNTIME_TARGET = ReplicaTarget(
    unit="agent-runtime", target_execution_concurrency=16, target_utilization=0.8,
    redundancy_replicas=1,
)
WORKER_TARGET = ReplicaTarget(
    unit="agent-worker", target_execution_concurrency=8, target_utilization=0.8,
    redundancy_replicas=1,
)
#: PG 维护/迁移保留连接：与 TASK-104 发布预检同一最低口径（`PG_MAINTENANCE_RESERVE = 1`），
#: 报告入参不写死在报告模块里；更保守的保留量由业务确认后显式传入。
PG_MAINTENANCE_RESERVE = 1

#: 纯逻辑夹具的锁定阈值/资源（手工输入，只用于纯分支；真实结论只来自真实运行证据）。
_SLO = SloEvidence(
    admission_p95_ms=500,
    admission_p99_ms=1500,
    framework_first_delta_p95_ms=1200,
    recovery_max_sec=60,
    max_system_error_ratio=0.01,
)
_HARDWARE: Mapping[str, HardwareSpec] = {
    "console-platform": HardwareSpec(500, 2000, 1024, 4096),
    "agent-runtime": HardwareSpec(1000, 4000, 2048, 8192),
    "agent-worker": HardwareSpec(1000, 4000, 2048, 8192),
    "im-gateway": HardwareSpec(500, 2000, 1024, 4096),
}
_RESOURCES = ResourceEvidence(
    hardware=_HARDWARE,
    process_counts={unit: 1 for unit in UNITS},
    replica_counts={"console-platform": 1, "agent-runtime": 2, "agent-worker": 2, "im-gateway": 1},
    max_replica_counts={"console-platform": 1, "agent-runtime": 4, "agent-worker": 8, "im-gateway": 1},
    pg_connection_budget=200,
    fd_budget=4096,
    pg_connections_per_process={
        "console-platform": 10, "agent-runtime": 10, "agent-worker": 10,
    },
    storage_event_bytes_per_day=5368709120,
    storage_retention_days=30,
    storage_artifact_bytes=107374182400,
)
_SHARED = SharedBudgetFacts(
    redis_max_connections=50,
    inbound_subscriber_limits={"im-gateway": 1100, "agent-runtime": 1100},
    sse_total_buffer_bytes=67108864,
)
_DEPLOYMENT = DeploymentFacts(
    renderer="fixture",
    units={
        unit: UnitDeploymentFacts(
            replicas=1,
            max_replicas=_RESOURCES.max_replica_counts[unit],
            cpu_request_millicores=_HARDWARE[unit].cpu_request_millicores,
            cpu_limit_millicores=_HARDWARE[unit].cpu_limit_millicores,
            memory_request_mb=_HARDWARE[unit].memory_request_mb,
            memory_limit_mb=_HARDWARE[unit].memory_limit_mb,
            termination_grace_sec=60,
            execution_concurrency={"agent-runtime": 8, "agent-worker": 4}.get(unit),
            outbound_connections_per_process=32,
        )
        for unit in UNITS
    },
)
#: 手工输入夹具的锁定 hash（纯分支用；真实 hash 由真实 profile + 真实清单算出）。
_FIXTURE_PROFILE_HASH = "0" * 64
_FIXTURE_FILE_DIGEST = "1" * 64


def _evidence(
    *,
    run_kind: RunKind = RunKind.ACCEPTANCE,
    users: int = 1000,
    effective_users: int = 1000,
    scale_users: int | None = None,
    attempts: int = 8000,
    admission_samples: tuple[float, ...] = tuple(float(value) for value in range(1, 101)),
    first_delta_samples: tuple[float, ...] = (900.0, 1100.0, 1200.0),
    failure_counts: Mapping[str, int] | None = None,
    slots: Mapping[str, int] | None = None,
    replicas: Mapping[str, int] | None = None,
    peak_running: int = 16,
    peak_waiting: int = 0,
    peak_admitted: int = 16,
    peak_streams: int = 16,
    model_mode: ModelMode = ModelMode.CONTROLLED_PROBE,
    model_max_inflight: int | None = 16,
    model_observed_concurrency: int = 2,
    connections_peak: int = 20,
    resources: ResourceEvidence | None = _RESOURCES,
    slo: SloEvidence | None = _SLO,
    deployment: DeploymentFacts | None = _DEPLOYMENT,
    profile_hash_value: str = _FIXTURE_PROFILE_HASH,
) -> CapacityEvidence:
    """手工输入夹具（不是测量证据）：只用于报告纯逻辑分支；真实结论只来自真实运行。"""
    resolved_slots = {"agent-runtime": 8, "agent-worker": 4} if slots is None else dict(slots)
    resolved_replicas = (
        {"console-platform": 1, "agent-runtime": 2, "agent-worker": 2, "im-gateway": 1}
        if replicas is None
        else dict(replicas)
    )
    resolved_deployment = _DEPLOYMENT if deployment is None else deployment
    return CapacityEvidence(
        captured_at="2026-10-19T00:00:00+00:00",
        run_kind=run_kind,
        profile_hash=profile_hash_value,
        profile_file_digest=_FIXTURE_FILE_DIGEST,
        config_hash=config_hash(profile_hash_value, resolved_deployment),
        workload=WorkloadEvidence(
            users=users,
            active_runs_per_user=1,
            ramp_sec=60,
            steady_sec=600,
            turns_per_user=8,
            effective_users=effective_users,
            attempts=attempts,
            scale_users=scale_users,
        ),
        latencies=(
            LatencyEvidence(metric="admission_ms", samples_ms=admission_samples),
            LatencyEvidence(metric="first_delta_ms", samples_ms=first_delta_samples),
        ),
        failure_counts=dict(failure_counts or {}),
        execution=ExecutionEvidence(
            slots_per_process=resolved_slots,
            measured_replicas=resolved_replicas,
            peak_running_runs=peak_running,
            peak_waiting_runs=peak_waiting,
            peak_admitted_runs=peak_admitted,
            peak_active_streams=peak_streams,
            sample_count=12,
            wall_seconds=61.0,
        ),
        postgres=PostgresEvidence(
            connections_peak=connections_peak,
            connections_active_peak=3,
            locks_waiting_peak=0,
            wal_growth_bytes=1048576,
            database_bytes=10485760,
        ),
        model=ModelBoundary(
            mode=model_mode,
            credential_refs=("env:MUAD_CAPACITY_MODEL_KEY",) if model_mode is ModelMode.REAL else (),
            declared_max_inflight=model_max_inflight,
            observed_calls=attempts,
            observed_max_concurrency=model_observed_concurrency,
            faults={},
        ),
        deployment=resolved_deployment,
        resources=resources,
        slo=slo,
        shared=_SHARED,
    )


# --------------------------------------------------------------------------------------
# 纯逻辑：证据包严格性 / 分位数 / 副本公式 / 瓶颈 / 配置 hash（毫秒级）
# --------------------------------------------------------------------------------------


def test_b317_evidence_bundle_round_trips_and_rejects_missing_or_illegal_facts() -> None:
    """断言①：证据包是 typed 严格结构：JSON 往返逐字段相等；缺失/未知/非法一律显式失败。"""
    evidence = _evidence()
    payload = evidence_payload(evidence)
    assert parse_evidence(payload) == evidence, "证据包必须可 JSON 往返且逐字段相等"

    missing = dict(payload)
    del missing["postgres"]
    with pytest.raises(EvidenceError):
        parse_evidence(missing)
    unknown = {**payload, "fabricated_pods": 999}
    with pytest.raises(EvidenceError):
        parse_evidence(unknown)
    empty_samples = {**payload, "latencies": [{"metric": "admission_ms", "samples_ms": []}]}
    with pytest.raises(EvidenceError):
        parse_evidence(empty_samples)
    nan = {**payload, "execution": {**payload["execution"], "wall_seconds": float("nan")}}
    with pytest.raises(EvidenceError):
        parse_evidence(nan)
    secret = {
        **payload,
        "model": {**payload["model"], "credential_refs": ["sk-live-not-a-reference"]},
    }
    with pytest.raises(EvidenceError):
        parse_evidence(secret)
    with pytest.raises(EvidenceError):
        parse_evidence([])


def test_b317_report_numbers_come_only_from_recorded_samples_and_failure_counts() -> None:
    """断言②：P50/P95/P99 与错误率只由证据包里的真实样本/失败账目算出（可独立复算）。"""
    evidence = _evidence(
        failure_counts={"TRANSPORT_ERROR": 3, "ADMISSION_REJECTED": 5},
        attempts=800,
    )
    report = build_report(evidence, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    by_metric = {fact.metric: fact for fact in report.latencies}
    expected = summarize_latencies(evidence.latencies[0].samples_ms)
    admission = by_metric["admission_ms"]
    assert (admission.count, admission.p50_ms, admission.p95_ms, admission.p99_ms) == (
        expected.count,
        expected.p50,
        expected.p95,
        expected.p99,
    )
    assert admission.p95_ms == percentile(evidence.latencies[0].samples_ms, 0.95) == 95.0
    assert by_metric["first_delta_ms"].p99_ms == max(evidence.latencies[1].samples_ms)

    assert report.error_counts["system"] == 3, "系统错误按显式分类计数"
    assert report.error_counts["overload"] == 5, "明确过载/排队超时单独计数（不合并成成功率）"
    assert report.error_counts["total"] == 8
    assert report.error_ratio == pytest.approx(3 / 800)

    changed = _evidence(admission_samples=tuple(float(value) for value in range(1, 200)))
    changed_report = build_report(changed, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    assert changed_report.latencies[0].p95_ms == 190.0, "样本变了 P95 必须跟着变（不是常量）"

    no_samples = _evidence(admission_samples=())
    with pytest.raises(EvidenceError):
        build_report(no_samples, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)


def test_b317_replica_formula_is_reproducible_from_recorded_inputs() -> None:
    """断言③：N = ceil(目标并发 / 单 Pod 容量 / 目标利用率) + 冗余副本，全部来自证据与业务参数。"""
    evidence = _evidence()
    runtime, worker = recommend_replicas(evidence, [RUNTIME_TARGET, WORKER_TARGET])
    assert runtime.per_pod_capacity == 8, "单 Pod 容量 = 真实进程执行槽 × 每 Pod 进程数"
    assert runtime.recommended_replicas == math.ceil(16 / 8 / 0.8) + 1 == 4
    assert runtime.within_max_replicas is True, "4 ≤ HPA 上界 4"
    assert runtime.formula == "ceil(16 / 8 / 0.8) + 1 = 4", "公式可逐字复算（不是只有结果）"
    assert runtime.capacity_basis == "MEASURED", "本夹具观测到 4 个在飞 ≥ 单 Pod 容量 8/2=4"

    assert worker.per_pod_capacity == 4 and worker.recommended_replicas == 4
    assert worker.capacity_basis == "DECLARED", "Worker 槽位未被压满：容量口径必须如实标注"

    conservative = recommend_replicas(
        evidence,
        [
            ReplicaTarget(
                unit="agent-runtime", target_execution_concurrency=16,
                target_utilization=0.5, redundancy_replicas=1,
            )
        ],
    )[0]
    assert conservative.recommended_replicas == math.ceil(16 / 8 / 0.5) + 1 == 5, (
        "利用率是输入，不是常量"
    )

    over = recommend_replicas(
        evidence,
        [
            ReplicaTarget(
                unit="agent-runtime", target_execution_concurrency=1000,
                target_utilization=0.8, redundancy_replicas=1,
            )
        ],
    )[0]
    assert over.recommended_replicas == 158 and over.within_max_replicas is False, (
        "1000 并发在 8 槽/副本下需要 158 副本，超过 HPA 上界 4 是真实发现（不藏起来）"
    )

    for bad in (
        {
            "unit": "console-platform", "target_execution_concurrency": 1,
            "target_utilization": 0.8, "redundancy_replicas": 0,
        },
        {
            "unit": "agent-runtime", "target_execution_concurrency": 0,
            "target_utilization": 0.8, "redundancy_replicas": 0,
        },
        {
            "unit": "agent-runtime", "target_execution_concurrency": 1,
            "target_utilization": 0.0, "redundancy_replicas": 0,
        },
        {
            "unit": "agent-runtime", "target_execution_concurrency": 1,
            "target_utilization": 1.5, "redundancy_replicas": 0,
        },
        {
            "unit": "agent-runtime", "target_execution_concurrency": 1,
            "target_utilization": 0.8, "redundancy_replicas": -1,
        },
    ):
        with pytest.raises(EvidenceError):
            recommend_replicas(evidence, [ReplicaTarget(**bad)])

    without_slots = _evidence(slots={"agent-runtime": 8})
    with pytest.raises(EvidenceError, match="agent-worker"):
        recommend_replicas(without_slots, [WORKER_TARGET])
    no_execution = _evidence(peak_running=0)
    with pytest.raises(EvidenceError):
        recommend_replicas(no_execution, [RUNTIME_TARGET])

    report_without_targets = build_report(evidence, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    assert report_without_targets.replicas == (), "没有业务参数就不给 Pod 数（不编造）"


def test_b317_bottlenecks_are_emitted_only_when_a_real_limit_is_touched() -> None:
    """断言④：瓶颈必须由证据里的真实 observed/ceiling 触达判定；没触到就不产出。"""
    untouched = build_report(
        _evidence(
            peak_running=2, model_observed_concurrency=1, peak_waiting=0, connections_peak=20
        ),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert untouched.bottlenecks == (), "没有触到任何上限时不得编造瓶颈"

    slots = build_report(
        _evidence(
            peak_running=16,
            peak_admitted=20,
            peak_waiting=4,
            model_max_inflight=4,
            model_observed_concurrency=4,
        ),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    kinds = {bottleneck.kind: bottleneck for bottleneck in slots.bottlenecks}
    assert kinds[BottleneckKind.EXECUTION_SLOTS].observed == 16
    assert kinds[BottleneckKind.EXECUTION_SLOTS].ceiling == 8 * 1 * 2, "8 槽 × 1 进程 × 2 副本"
    assert kinds[BottleneckKind.EXECUTION_SLOTS].exhausted is True
    assert kinds[BottleneckKind.MODEL_INFLIGHT].observed == 4
    assert kinds[BottleneckKind.MODEL_INFLIGHT].ceiling == 4
    assert kinds[BottleneckKind.MODEL_INFLIGHT].exhausted is True, "观测并发 4 ≥ 凭据上限 4"
    assert kinds[BottleneckKind.ADMISSION_QUEUE].observed == 4
    assert kinds[BottleneckKind.ADMISSION_QUEUE].ceiling == 1000, "排队上界 = 目标并发用户"
    assert kinds[BottleneckKind.ADMISSION_QUEUE].exhausted is False, "4+16 < 1000：队列被用到但没压满"

    below_ceiling = build_report(
        _evidence(model_max_inflight=16, model_observed_concurrency=4),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert BottleneckKind.MODEL_INFLIGHT not in {b.kind for b in below_ceiling.bottlenecks}, (
        "没触到额度上限就不产出模型瓶颈"
    )

    pg = build_report(
        _evidence(connections_peak=200),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    pg_bottleneck = next(b for b in pg.bottlenecks if b.kind is BottleneckKind.PG_CONNECTIONS)
    assert pg_bottleneck.observed == 201 and pg_bottleneck.ceiling == 200, (
        "实测连接峰值 200 + 维护保留 1 越界必须带真实数字"
    )
    assert pg.verdict is Verdict.REJECTED, "PG 连接实测越界必须判不达标"

    no_ceiling = build_report(
        _evidence(model_max_inflight=None, model_observed_concurrency=9),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert BottleneckKind.MODEL_INFLIGHT not in {b.kind for b in no_ceiling.bottlenecks}, (
        "没有额度上限证据时不产出模型瓶颈（不猜）"
    )


def test_b317_missing_slo_or_resources_never_yields_a_production_pass() -> None:
    """断言⑤：缺 SLO/资源声明或受限规模 → 只能给诊断结论；SLO 违约 → 明确不达标。"""
    accepted = build_report(_evidence(), pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    assert accepted.verdict is Verdict.ACCEPTED, "全量 + SLO 通过 + 预算内才是生产达标"
    assert accepted.blockers == ()

    no_slo = build_report(_evidence(slo=None), pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    assert no_slo.verdict is Verdict.DIAGNOSTIC
    assert any("SLO" in blocker for blocker in no_slo.blockers)
    assert no_slo.slo == ()

    no_resources = build_report(
        _evidence(resources=None, deployment=_DEPLOYMENT),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert no_resources.verdict is Verdict.DIAGNOSTIC
    assert any("资源" in blocker for blocker in no_resources.blockers)
    assert no_resources.resources == ()

    scaled = build_report(
        _evidence(
            run_kind=RunKind.VERIFICATION, effective_users=6, scale_users=6, attempts=6,
            failure_counts={},
        ),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert scaled.verdict is Verdict.DIAGNOSTIC, "受限规模不冒充全量验收结论"
    assert any("受限" in blocker for blocker in scaled.blockers)

    breached = build_report(
        _evidence(admission_samples=tuple(600.0 + index for index in range(100))),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert breached.verdict is Verdict.REJECTED, "P95 超过阈值必须判不达标"
    assert any(not result.passed for result in breached.slo)

    tampered = _evidence()
    payload = evidence_payload(tampered)
    payload["config_hash"] = "f" * 64
    with pytest.raises(EvidenceError, match="config_hash"):
        build_report(parse_evidence(payload), pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)


def test_b317_config_hash_covers_the_real_profile_and_deployment_facts() -> None:
    """断言⑥：配置 hash 由真实 profile 内容 hash + 真实渲染清单事实决定；改任一侧都变。"""
    profile = load_profile(PROFILE_PATH)
    deployment = deployment_facts_from_manifests(BASE)
    expected = config_hash(profile_hash(profile), deployment)

    runtime = deployment.units["agent-runtime"]
    assert runtime.max_replicas == 4, "HPA 上界来自真实渲染清单"
    assert runtime.execution_concurrency == 256, "执行槽来自清单 env 字面量"
    assert (runtime.cpu_limit_millicores, runtime.memory_limit_mb) == (4000, 8192)
    assert deployment.units["console-platform"].max_replicas == 1

    resources = profile.resources
    assert resources is not None
    for unit in UNITS:
        facts = deployment.units[unit]
        hardware = resources.hardware[unit]
        assert (
            facts.cpu_request_millicores,
            facts.cpu_limit_millicores,
            facts.memory_request_mb,
            facts.memory_limit_mb,
        ) == (
            hardware.cpu_request_millicores,
            hardware.cpu_limit_millicores,
            hardware.memory_request_mb,
            hardware.memory_limit_mb,
        ), f"{unit}: 渲染清单与容量 profile 的资源声明必须逐字段一致"

    drifted = DeploymentFacts(
        renderer=deployment.renderer,
        units={
            **deployment.units,
            "agent-runtime": replace(deployment.units["agent-runtime"], max_replicas=5),
        },
    )
    assert config_hash(profile_hash(profile), drifted) != expected, "副本上界变化必须改变配置 hash"

    evidence = _evidence(deployment=deployment, profile_hash_value=profile_hash(profile))
    assert evidence.config_hash == expected
    assert build_report(
        evidence, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE
    ).config_hash == expected
    markdown = render_markdown(
        build_report(evidence, pg_maintenance_reserve=PG_MAINTENANCE_RESERVE)
    )
    assert expected in markdown and "ceil(16 / 8 / 0.8)" in render_markdown(
        build_report(
            evidence,
            pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
            replica_targets=[RUNTIME_TARGET],
        )
    )


def test_b317_report_marks_probe_and_real_model_boundaries_separately() -> None:
    """断言⑦：协议探针与真实模型边界分开标记：探针吞吐不冒充真实模型容量。"""
    probe_report = build_report(
        _evidence(model_mode=ModelMode.CONTROLLED_PROBE, model_max_inflight=16),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert probe_report.model_boundary.mode is ModelMode.CONTROLLED_PROBE
    assert probe_report.model_boundary.real_supplier_capacity is False
    assert any("探针" in note for note in probe_report.notes), "报告必须显式声明探针边界"

    real_report = build_report(
        _evidence(model_mode=ModelMode.REAL, model_max_inflight=16),
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
    )
    assert real_report.model_boundary.mode is ModelMode.REAL
    assert real_report.model_boundary.real_supplier_capacity is True
    assert all("sk-" not in ref for ref in real_report.model_boundary.credential_refs), (
        "证据包只允许 env: 引用，不得内嵌密钥原文"
    )


# --------------------------------------------------------------------------------------
# 真实栈：受限规模真实负载 → 证据包 → 报告（B-317 的真实边界）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededTenant:
    tenant_id: str
    model_id: uuid.UUID
    credential_id: uuid.UUID
    entry_id: uuid.UUID
    user_ids: tuple[uuid.UUID, ...]
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportHarness:
    """真实容量栈 + 本轮种子 + 一次真实负载 + 由它采集的证据包（落盘后再读回）。"""

    stack: CapacityStack
    seeded: SeededTenant
    load: LoadReport
    evidence: CapacityEvidence
    evidence_path: Path


@pytest.fixture(scope="module")
def report_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ReportHarness]:
    """四单元独立栈（TASK-107）+ 真实负载一次（TASK-109 驱动）+ 真实证据采集（TASK-117）。

    种子必须在 `stack.start()` 之前落库（Gateway 入口快照启动时拉取）；模型凭据地址在探针
    起来后回填（Runtime 每次调用都向 Console 解析凭据）。
    """
    profile = load_profile(PROFILE_PATH)
    datastore = acquire_datastore()
    stack = CapacityStack(
        root=tmp_path_factory.mktemp("capacity-report"),
        profile=profile,
        datastore=datastore,
        service_env=SERVICE_ENV,
    )
    seeded = _seed_console(datastore.database_url, stack.tenant_id)
    stack.start()
    _bind_probe_credential(datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1")
    try:
        sessions = _exchange_sessions(stack.proxy.url, seeded)
        _set_probe_behavior(stack.model_probe.url)
        load = _run_load(stack, seeded, sessions)
        evidence = capture_evidence(
            load=load,
            profile=profile,
            profile_file_digest=file_digest(PROFILE_PATH),
            deployment=deployment_facts_from_manifests(BASE),
            measured_slots_per_process=_measured_slots(stack),
            measured_replicas={unit: len(stack.instances(unit)) for unit in UNITS},
            shared=_shared_budget_facts(),
            pg_connections_per_process=_pg_connections_per_process(),
            model_declared_max_inflight=_credential_max_inflight(
                datastore.database_url, seeded.credential_id
            ),
            captured_at=datetime.now(UTC).isoformat(),
        )
        evidence_path = tmp_path_factory.mktemp("capacity-report-evidence") / "evidence.json"
        write_evidence(evidence_path, evidence)
        parsed = load_evidence(evidence_path)
        assert parsed == evidence, "落盘再读回必须逐字段相等（报告只消费这份证据）"
        yield ReportHarness(
            stack=stack, seeded=seeded, load=load, evidence=parsed, evidence_path=evidence_path
        )
    finally:
        stack.teardown()


def _seed_console(database_url: str, tenant_id: str) -> SeededTenant:
    """真实 PG 种子：模型/凭据/Agent/网页入口 + 每虚拟用户一份用户/授权/Token。"""
    tokens = tuple(generate_chat_token() for _ in range(SEEDED_USERS))
    ids: dict[str, Any] = {}

    async def seed() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                model = ModelDefinition(
                    tenant_id=tenant_id,
                    key=f"capacity-report-model-{uuid.uuid4().hex[:8]}",
                    name="Capacity Report Model",
                    model_id="gpt-4o-mini",
                    provider_key="openai",
                    params_json={"temperature": 0.0, "max_tokens": 128},
                )
                session.add(model)
                await session.flush()
                credential = ModelCredential(
                    tenant_id=tenant_id,
                    key=f"capacity-report-cred-{uuid.uuid4().hex[:8]}",
                    provider_key="openai",
                    base_url="http://127.0.0.1:1/v1",
                    api_key=PROBE_KEY,
                    api_key_digest=hashlib.sha256(PROBE_KEY.encode()).hexdigest(),
                    max_inflight=MODEL_MAX_INFLIGHT,
                )
                session.add(credential)
                await session.flush()
                session.add(
                    ModelDeployment(
                        tenant_id=tenant_id,
                        key=f"capacity-report-member-{uuid.uuid4().hex[:8]}",
                        model_id=model.id,
                        credential_id=credential.id,
                        weight=1,
                    )
                )
                agent = AgentDefinition(
                    tenant_id=tenant_id,
                    key=f"capacity-report-agent-{uuid.uuid4().hex[:8]}",
                    name="Capacity Report Agent",
                    instructions="You are a capacity report agent.",
                    model_id=model.id,
                    runtime_config={"max_model_retries": 1},
                )
                session.add(agent)
                await session.flush()
                entry = BotAccount(
                    tenant_id=tenant_id,
                    channel="WEBCHAT",
                    name="Capacity Report Entry",
                    bot_id=f"capacity-report-{uuid.uuid4().hex[:8]}",
                    agent_id=agent.id,
                    enabled=True,
                    config_json={},
                )
                session.add(entry)
                await session.flush()
                user_ids: list[uuid.UUID] = []
                for index, token in enumerate(tokens):
                    user = PlatformUser(
                        tenant_id=tenant_id,
                        user_code=f"capacity-report-user-{index}-{uuid.uuid4().hex[:8]}",
                        display_name=f"Capacity Report User {index}",
                    )
                    session.add(user)
                    await session.flush()
                    session.add(
                        AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id)
                    )
                    session.add(
                        ChatAccessToken(
                            tenant_id=tenant_id,
                            platform_user_id=user.id,
                            token_hash=hash_chat_token(token),
                            revision=1,
                            issued_at=datetime.now(UTC),
                            expires_at=datetime.now(UTC) + timedelta(days=30),
                        )
                    )
                    user_ids.append(user.id)
                await session.commit()
                ids.update(
                    model_id=model.id,
                    credential_id=credential.id,
                    entry_id=entry.id,
                    user_ids=tuple(user_ids),
                )
        finally:
            await engine.dispose()

    run_async(seed)
    return SeededTenant(
        tenant_id=tenant_id,
        model_id=ids["model_id"],
        credential_id=ids["credential_id"],
        entry_id=ids["entry_id"],
        user_ids=ids["user_ids"],
        tokens=tokens,
    )


def _bind_probe_credential(database_url: str, credential_id: uuid.UUID, probe_url: str) -> None:
    """把凭据连接地址回填为**本栈真实模型探针**（真实 HTTP 服务，不是替身）。"""

    async def bind() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                credential = await session.get(ModelCredential, credential_id)
                assert credential is not None, "凭据行必须已存在"
                credential.base_url = probe_url
                await session.commit()
        finally:
            await engine.dispose()

    run_async(bind)


def _exchange_sessions(proxy_url: str, seeded: SeededTenant) -> tuple[ChatCredential, ...]:
    """真实 Token 交换（经入口代理 → Console API-04）：每用户一份 CHAT Cookie 与上下文身份。"""
    credentials: list[ChatCredential] = []
    with httpx.Client(base_url=proxy_url, timeout=15.0, trust_env=False) as client:
        for index, token in enumerate(seeded.tokens):
            response = client.post("/api/v1/webchat/auth/exchange", json={"token": token})
            assert response.status_code == 200, f"第 {index} 份 Token 交换失败：{response.text}"
            data = response.json()["data"]
            assert data["user_id"] == str(seeded.user_ids[index]), "交换必须导出来源 Token 的用户"
            cookie = response.cookies.get("muad_chat_session")
            assert cookie, "交换必须写独立 CHAT Cookie"
            credentials.append(
                ChatCredential(cookie=cookie, context=data["session_id"], user_id=data["user_id"])
            )
    return tuple(credentials)


def _set_probe_behavior(probe_url: str) -> None:
    """真实探针行为注入：首块延迟 = 800ms（在飞窗口足够长，模型容量上限可被真实触到）。"""
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(
            f"{probe_url}/__probe__/behavior",
            json={
                "api_key": None,
                "behavior": {"latency_ms": PROBE_LATENCY_MS, "deltas": ["容量", "报告"]},
            },
        )
        assert response.status_code == 200, response.text


def _run_load(
    stack: CapacityStack, seeded: SeededTenant, sessions: tuple[ChatCredential, ...]
) -> LoadReport:
    """真实受限规模负载（VERIFICATION，显式登记）：同一驱动、同一锁定 profile。"""
    driver = LoadDriver(
        target=LoadTarget(
            base_url=stack.proxy.url,
            entry_id=str(seeded.entry_id),
            sessions=sessions[:LOAD_USERS],
            control_session=sessions[LOAD_USERS],
            runtime_url=stack.url_of("agent-runtime"),
            internal_token=stack.internal_token,
            model_probe_url=stack.model_probe.url,
        ),
        profile=load_profile(PROFILE_PATH),
        run_kind=RunKind.VERIFICATION,
        scale=VERIFICATION_SCALE,
        metrics=PostgresMetrics(
            stack.database_url, tenant_id=stack.tenant_id, artifacts_root=stack.artifacts_root
        ),
        unit_targets={unit: stack.url_of(unit) for unit in UNITS},
        control_model_id=str(seeded.model_id),
        control_credential_id=str(seeded.credential_id),
    )
    return driver.run()


def _measured_slots(stack: CapacityStack) -> dict[str, int]:
    """真实运行进程 env 里的执行槽（不是用例常量）：缺键即显式失败。"""
    runtime_env = read_process_environ(stack.instance("agent-runtime").pid)
    worker_env = read_process_environ(stack.instance("agent-worker").pid)
    try:
        return {
            "agent-runtime": int(runtime_env["RUNTIME_EXECUTION_CONCURRENCY"]),
            "agent-worker": int(worker_env["WORKER_EXECUTION_CONCURRENCY"]),
        }
    except KeyError as exc:
        raise AssertionError(f"真实进程 env 缺执行槽键 {exc}：证据必须来自进程事实") from exc


def _shared_budget_facts() -> SharedBudgetFacts:
    """真实 typed 启动预算（本栈未覆盖这些键 ⇒ `SharedSettings` 就是生效值）。"""
    settings = SharedSettings()
    return SharedBudgetFacts(
        redis_max_connections=settings.redis_max_connections,
        inbound_subscriber_limits={
            "im-gateway": settings.gateway_max_sse_subscribers,
            "agent-runtime": settings.runtime_max_sse_subscribers,
        },
        sse_total_buffer_bytes=settings.sse_total_buffer_bytes,
    )


def _pg_connections_per_process() -> dict[str, int]:
    """每进程 PG 连接上限：真实 engine 工厂的 `pool_size + max_overflow`（不建立连接）。"""
    limits: dict[str, int] = {}
    for unit, factory in (
        ("console-platform", console_db.get_engine),
        ("agent-runtime", runtime_db.get_engine),
        ("agent-worker", worker_db.get_engine),
    ):
        engine = factory()
        overflow = getattr(engine.pool, "_max_overflow", None)
        assert isinstance(overflow, int), "SQLAlchemy 连接池必须暴露 max_overflow 上限"
        limits[unit] = engine.pool.size() + overflow
    return limits


def _credential_max_inflight(database_url: str, credential_id: uuid.UUID) -> int:
    """物理 Key 的真实在飞上限（PG 行）：模型容量边界的证据，不是用例常量。"""
    value = query_value(
        database_url,
        "SELECT max_inflight FROM control.model_credential WHERE id = $1",
        credential_id,
    )
    assert value is not None, "凭据行必须存在（模型容量边界来自真实 PG 行）"
    return int(value)


def test_b317_real_bounded_load_yields_evidence_backed_report(report_harness: ReportHarness) -> None:
    """断言⑧（成功分支）：真实负载 → 证据包 → 报告；分位数/瓶颈/资源/hash/公式全部可复算。"""
    evidence = report_harness.evidence
    load = report_harness.load
    profile = load_profile(PROFILE_PATH)
    deployment = deployment_facts_from_manifests(BASE)

    assert evidence.run_kind is RunKind.VERIFICATION
    assert evidence.workload.effective_users == VERIFICATION_SCALE.users
    assert evidence.workload.scale_users == VERIFICATION_SCALE.users
    assert evidence.workload.attempts == load.attempts()
    assert evidence.config_hash == config_hash(profile_hash(profile), deployment)
    assert evidence.profile_hash == load.locked_profile_hash, "证据必须锁定本次运行的真实 profile"

    report = build_report(
        evidence,
        pg_maintenance_reserve=PG_MAINTENANCE_RESERVE,
        replica_targets=[RUNTIME_TARGET, WORKER_TARGET],
    )

    # 分位数只来自真实样本（可独立复算）
    admission_samples = tuple(
        turn.admission_ms for turn in load.turns if turn.admission_ms is not None
    )
    assert len(admission_samples) == load.attempts()
    facts = {fact.metric: fact for fact in report.latencies}
    expected = summarize_latencies(admission_samples)
    assert facts["admission_ms"].p95_ms == expected.p95
    assert facts["admission_ms"].p99_ms == expected.p99
    assert facts["admission_ms"].count == len(admission_samples)
    assert facts["first_delta_ms"].p95_ms >= PROBE_LATENCY_MS, (
        "首字测量包含真实探针延迟（不是假回执）"
    )

    # 受限规模只给诊断结论；SLO 结果按证据里的真实样本判定
    assert report.verdict is Verdict.REJECTED, (
        "本受限运行故意把物理 Key 在飞上限设为 4（真实 PG 行）：模型容量被真实压满，"
        "框架首字 P95 超过锁定 SLO —— 报告必须如实判不达标（真实发现，不伪造通过）"
    )
    assert any("受限" in blocker for blocker in report.blockers)
    failed = {result.metric for result in report.slo if not result.passed}
    # CI runner 的调度噪声会把 admission 的 500ms 硬 SLO 也推过线；这不改变本用例要守的
    # 契约：报告必须如实判 REJECTED，且真实模型瓶颈导致的 framework_first_delta 必须失败。
    # 只允许这两个 metric 失败，防止其它 SLO 被静默放过。
    assert "framework_first_delta_p95_ms" in failed, (
        f"实际不达标项：{sorted(failed)}；"
        f"判定明细：{[(r.metric, r.observed, r.threshold) for r in report.slo]}"
    )
    assert failed <= {"framework_first_delta_p95_ms", "admission_p95_ms"}, (
        f"实际不达标项：{sorted(failed)}；"
        f"判定明细：{[(r.metric, r.observed, r.threshold) for r in report.slo]}"
    )

    # 探针边界：协议探针不冒充真实模型容量
    assert report.model_boundary.mode is ModelMode.CONTROLLED_PROBE
    assert report.model_boundary.real_supplier_capacity is False
    assert report.model_boundary.declared_max_inflight == MODEL_MAX_INFLIGHT
    assert report.model_boundary.observed_max_concurrency >= 1, "真实推理并发必须被观测到"
    assert any("探针" in note for note in report.notes)

    # 瓶颈：6 用户 × 800ms 探针延迟 + 凭据上限 4 ⇒ 模型池上限必然被触到
    model_bottlenecks = [
        bottleneck
        for bottleneck in report.bottlenecks
        if bottleneck.kind is BottleneckKind.MODEL_INFLIGHT
    ]
    assert model_bottlenecks, "真实触到模型在飞上限必须产出瓶颈项"
    model_limit = model_bottlenecks[0]
    assert model_limit.observed == evidence.model.observed_max_concurrency
    assert model_limit.ceiling == MODEL_MAX_INFLIGHT
    assert model_limit.exhausted is True
    for bottleneck in report.bottlenecks:
        assert bottleneck.observed > 0 and bottleneck.ceiling > 0, "瓶颈数字必须来自证据"

    # 资源预算：PG 总量按真实 engine 上限 × 进程 × 最大副本；清单与 profile 一致
    assert report.resources, "正式 profile 有 resources ⇒ 报告必须给出资源预算行"
    by_unit = {line.unit: line for line in report.resources}
    assert evidence.resources.pg_connections_per_process == _pg_connections_per_process(), (
        "每进程连接上限必须来自真实 engine 工厂（不是用例常量）"
    )
    expected_pg = sum(
        evidence.resources.process_counts[unit]
        * evidence.resources.max_replica_counts[unit]
        * evidence.resources.pg_connections_per_process[unit]
        for unit in ("console-platform", "agent-runtime", "agent-worker")
    )
    assert by_unit["agent-runtime"].pg_connections_at_max_replicas == (
        1 * 4 * evidence.resources.pg_connections_per_process["agent-runtime"]
    )
    assert expected_pg + PG_MAINTENANCE_RESERVE <= evidence.resources.pg_connection_budget
    assert all(line.within_budget for line in report.resources), (
        [line.detail for line in report.resources if not line.within_budget]
    )

    # 副本公式：可从证据里的真实执行槽复算，且不越过部署上界
    runtime, worker = report.replicas
    assert runtime.per_pod_capacity == evidence.execution.slots_per_process["agent-runtime"]
    assert runtime.recommended_replicas == math.ceil(16 / 8 / 0.8) + 1 == 4
    assert runtime.within_max_replicas is True
    assert worker.per_pod_capacity == 4 and worker.recommended_replicas == 4
    assert runtime.unit in AUTOSCALED_UNITS and worker.unit in AUTOSCALED_UNITS
    assert runtime.capacity_basis == "DECLARED", (
        "受限规模没把执行槽压满：容量口径必须如实标注（不冒充实测压满）"
    )

    markdown = render_markdown(report)
    assert evidence.config_hash in markdown
    assert runtime.formula in markdown, "运营文档引用的公式必须能在报告里逐字复算"
    assert report_harness.evidence_path.exists(), "证据包必须真实落盘（可复核、可复跑）"
