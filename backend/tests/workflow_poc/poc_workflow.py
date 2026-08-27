"""ADR-WF-001 build-vs-buy PoC harness 公共件（TASK-001 落地）。

被 TASK-003（DBOS）/ TASK-004（Restate）/ TASK-002（Temporal）复用，保证三候选
在完全相同的口径下产出可对比证据：

- `POC_WORKFLOW_STEPS` — 统一 5-step 最小 durable workflow 定义（roadmap TASK-0002 项 1）
- `PocCriterion` / `PocCriteriaReport` — 7 口径断言框架
  （P-CRASH / P-TIMER / P-IDEMP / P-PIN / P-TIMEOUT / P-SCALE / P-SIGNAL）
- `TraceCorrelator` — trace_id/run_id/tenant_id 关联记录 + SLO-OBS-01 完整性断言
  （roadmap TASK-0002 项 9：trace integration）
- `MockRetentionGuard` — P-PIN retention mock（`active_references` 未实现，
  全真验属 ADR-SNAPSHOT-001 实现任务）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, slots=True)
class PocStepSpec:
    """PoC workflow 单 step 定义。"""

    name: str
    kind: str
    description: str


POC_WORKFLOW_STEPS: tuple[PocStepSpec, ...] = (
    PocStepSpec(
        name="write_report_record",
        kind="idempotent-write",
        description="幂等写：以 run_id 为幂等键写入业务记录，重复执行不产生第二条",
    ),
    PocStepSpec(
        name="wait_review_window",
        kind="timer",
        description="durable timer：跨进程重启存活的时间等待",
    ),
    PocStepSpec(
        name="fetch_external_data",
        kind="timeout",
        description="外部调用：带超时上限与失败策略，禁止无限等待",
    ),
    PocStepSpec(
        name="await_approval",
        kind="external-approval-signal",
        description="外部审批信号：等待外部 approve signal 唤醒（HumanTask 语义）",
    ),
    PocStepSpec(
        name="notify_http_endpoint",
        kind="http-activity",
        description="HTTP activity：出站 HTTP 调用作为 workflow step（Agent/HTTP activity）",
    ),
)


class PocCriterion(str, Enum):
    """7 口径（design §3.1.3 + roadmap TASK-0002 对齐，P-SIGNAL 为补充项）。"""

    CRASH = "P-CRASH"
    TIMER = "P-TIMER"
    IDEMP = "P-IDEMP"
    PIN = "P-PIN"
    TIMEOUT = "P-TIMEOUT"
    SCALE = "P-SCALE"
    SIGNAL = "P-SIGNAL"


@dataclass(frozen=True, slots=True)
class CriterionOutcome:
    """单口径断言结果；evidence 必须指向可复核的观测记录（日志/DB/trace）。"""

    criterion: PocCriterion
    passed: bool
    detail: str
    evidence: str = ""


@dataclass(slots=True)
class PocCriteriaReport:
    """单候选 7 口径聚合；TASK-005 据此回填评估矩阵。"""

    outcomes: list[CriterionOutcome] = field(default_factory=list)

    def add(self, outcome: CriterionOutcome) -> None:
        self.outcomes.append(outcome)

    def all_passed(self) -> bool:
        passed = {outcome.criterion for outcome in self.outcomes if outcome.passed}
        return len(self.outcomes) == len(PocCriterion) and passed == set(PocCriterion)

    def to_dict(self) -> dict[str, object]:
        return {
            "outcomes": [
                {
                    "criterion": outcome.criterion.value,
                    "passed": outcome.passed,
                    "detail": outcome.detail,
                    "evidence": outcome.evidence,
                }
                for outcome in self.outcomes
            ],
            "all_passed": self.all_passed(),
        }


@dataclass(frozen=True, slots=True)
class CorrelatedEvent:
    event: str
    trace_id: str
    run_id: str
    tenant_id: str


@dataclass(slots=True)
class TraceCorrelator:
    """SLO-OBS-01：P0 路径 trace 关联完整率 ≥99% 的记录与断言。"""

    events: list[CorrelatedEvent] = field(default_factory=list)

    def record(
        self, event: str, *, trace_id: str, run_id: str, tenant_id: str
    ) -> None:
        self.events.append(
            CorrelatedEvent(
                event=event, trace_id=trace_id, run_id=run_id, tenant_id=tenant_id
            )
        )

    @property
    def total_events(self) -> int:
        return len(self.events)

    @property
    def correlated_events(self) -> int:
        return sum(
            1
            for item in self.events
            if item.trace_id and item.run_id and item.tenant_id
        )

    def completeness(self) -> float:
        if not self.events:
            return 1.0
        return self.correlated_events / self.total_events

    def assert_slo_obs01(self, *, min_ratio: float = 0.99) -> None:
        ratio = self.completeness()
        assert ratio >= min_ratio, (
            f"SLO-OBS-01 violation: trace correlation {ratio:.4f} < {min_ratio:.4f} "
            f"({self.correlated_events}/{self.total_events} correlated)"
        )


class RetentionBlockedError(Exception):
    """P-PIN mock：存在 active workflow 引用时删除被拒绝（RULE-WF-03 语义）。"""


@dataclass(slots=True)
class MockRetentionGuard:
    """active_references 的内存 mock。

    仅用于 PoC P-PIN 口径演练 retention 交互语义；`active_references` 表落库后
    由 ADR-SNAPSHOT-001 的实现替换，届时删除本 mock。
    """

    refs: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)

    def acquire(
        self, *, resource_type: str, resource_id: str, version: str, run_id: str
    ) -> None:
        key = (resource_type, resource_id, version)
        self.refs.setdefault(key, set()).add(run_id)

    def release(
        self, *, resource_type: str, resource_id: str, version: str, run_id: str
    ) -> None:
        key = (resource_type, resource_id, version)
        holders = self.refs.get(key)
        if holders is None:
            return
        holders.discard(run_id)
        if not holders:
            del self.refs[key]

    def assert_delete_allowed(
        self, *, resource_type: str, resource_id: str, version: str
    ) -> None:
        key = (resource_type, resource_id, version)
        if key in self.refs:
            raise RetentionBlockedError(
                f"active workflow references exist for {resource_type}/{resource_id}@{version}"
            )
