"""Two-arm replay: baseline vs candidate over Golden Set cases.

回放是 worker 内的离线能力（`WORKLOAD = BACKGROUND`）：基线臂结果按
`(case 集合, base_config_hash)` 复用，候选臂每次重跑；报告 append-only 落 eval_report。
`CaseRunner` 是执行端口（真实实现用冻结 Snapshot + 模型池），本模块只负责编排、判定与报告。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from muad_contracts.canonical import canonical_json

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import EvalCase, EvalReport
from ..metrics import EXPERIENCE_REPLAY_METRIC, LEARNING_PIPELINE_FAILURES_METRIC, record_counter

logger = logging.getLogger(__name__)

#: 回放只允许在后台工作负载里执行（B-06 架构断言）。
WORKLOAD = "BACKGROUND"

PASSED = "PASSED"
REJECTED = "REJECTED"
INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class ReplayLimits:
    min_cases: int = 10
    max_cases: int = 50
    min_improvement: float = 0.05


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    passed: bool
    reason: str = ""
    items_total: int = 1
    items_passed: int = 1

    @property
    def items_verified(self) -> bool:
        return self.items_total >= 1 and self.items_passed == self.items_total

    @property
    def case_passed(self) -> bool:
        return self.passed and self.items_verified


#: `(case, experience_key or None) -> 逐项结果`；异常由 `run_safely` 降级。
CaseRunner = Callable[[EvalCase, "str | None"], Awaitable[CaseOutcome]]


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    cases: int
    passed: int
    items_total: int
    items_passed: int

    @property
    def success_rate(self) -> float:
        return self.passed / self.cases if self.cases else 0.0

    def as_json(self) -> dict[str, float | int]:
        return {
            "cases": self.cases,
            "passed": self.passed,
            "items_total": self.items_total,
            "items_passed": self.items_passed,
            "success_rate": self.success_rate,
        }


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    status: str
    improvement: float
    baseline: ArmMetrics
    candidate: ArmMetrics


class LearningReplay:
    def __init__(
        self,
        session_factory: SessionFactory,
        runner: CaseRunner,
        limits: ReplayLimits | None = None,
    ) -> None:
        self._factory = session_factory
        self._runner = runner
        self._limits = limits or ReplayLimits()

    async def run_safely(
        self,
        tenant_id: str,
        task_family: str,
        *,
        experience_key: str,
        base_config_hash: str,
        refresh_baseline: bool = False,
    ) -> ReplayDecision | None:
        """学习链路失败降级为无判定：记 warning 与失败计数，不向调用方抛错。"""
        try:
            return await self.run(
                tenant_id,
                task_family,
                experience_key=experience_key,
                base_config_hash=base_config_hash,
                refresh_baseline=refresh_baseline,
            )
        except Exception as exc:  # noqa: BLE001 - 学习链路必须降级为无学习
            record_counter(
                LEARNING_PIPELINE_FAILURES_METRIC, labels={"stage": "replay", "outcome": "ERROR"}
            )
            logger.warning(
                "learning_replay_failed",
                extra={
                    "tenant_id": tenant_id,
                    "task_family": task_family,
                    "error_type": type(exc).__name__,
                },
            )
            return None

    async def run(
        self,
        tenant_id: str,
        task_family: str,
        *,
        experience_key: str,
        base_config_hash: str,
        refresh_baseline: bool = False,
    ) -> ReplayDecision:
        limits = self._limits
        async with self._factory() as session:
            cases = list(
                (
                    await session.scalars(
                        sa.select(EvalCase)
                        .where(
                            EvalCase.tenant_id == tenant_id,
                            EvalCase.task_family == task_family,
                            EvalCase.is_deleted.is_(False),
                        )
                        .order_by(EvalCase.create_time)
                        .limit(limits.max_cases)
                    )
                ).all()
            )
        if len(cases) < limits.min_cases:
            baseline = await self._arm(cases, None)
            candidate = await self._arm(cases, experience_key)
            await self._write_report(
                tenant_id,
                task_family,
                experience_key,
                base_config_hash,
                baseline,
                candidate,
                INCOMPLETE,
                0.0,
                cases,
            )
            record_counter(EXPERIENCE_REPLAY_METRIC, labels={"result": INCOMPLETE})
            return ReplayDecision(INCOMPLETE, 0.0, baseline, candidate)

        cached = await self._cached_baseline(tenant_id, task_family, base_config_hash)
        if cached is None or refresh_baseline:
            baseline = await self._arm(cases, None)
            await self._write_report(
                tenant_id,
                task_family,
                None,
                base_config_hash,
                baseline,
                baseline,
                "BASELINE",
                0.0,
                cases,
            )
        else:
            baseline = cached
        candidate = await self._arm(cases, experience_key)
        improvement = candidate.success_rate - baseline.success_rate
        status = PASSED if improvement >= limits.min_improvement else REJECTED
        await self._write_report(
            tenant_id,
            task_family,
            experience_key,
            base_config_hash,
            baseline,
            candidate,
            status,
            improvement,
            cases,
        )
        record_counter(EXPERIENCE_REPLAY_METRIC, labels={"result": status})
        return ReplayDecision(status, improvement, baseline, candidate)

    async def _arm(self, cases: Sequence[EvalCase], experience_key: str | None) -> ArmMetrics:
        passed = items_total = items_passed = 0
        for case in cases:
            outcome = await self._runner(case, experience_key)
            items_total += outcome.items_total
            items_passed += outcome.items_passed
            if outcome.case_passed:
                passed += 1
        return ArmMetrics(len(cases), passed, items_total, items_passed)

    async def _cached_baseline(
        self, tenant_id: str, task_family: str, base_config_hash: str
    ) -> ArmMetrics | None:
        async with self._factory() as session:
            row = await session.scalar(
                sa.select(EvalReport)
                .where(
                    EvalReport.tenant_id == tenant_id,
                    EvalReport.task_family == task_family,
                    EvalReport.experience_key.is_(None),
                    EvalReport.base_config_hash == base_config_hash,
                    EvalReport.is_deleted.is_(False),
                )
                .order_by(EvalReport.version.desc())
                .limit(1)
            )
        if row is None:
            return None
        metrics = cast(dict[str, Any], row.metrics_json)
        return ArmMetrics(
            int(metrics["cases"]),
            int(metrics["passed"]),
            int(metrics["items_total"]),
            int(metrics["items_passed"]),
        )

    async def _write_report(
        self,
        tenant_id: str,
        task_family: str,
        experience_key: str | None,
        base_config_hash: str,
        baseline: ArmMetrics,
        candidate: ArmMetrics,
        status: str,
        improvement: float,
        cases: Sequence[EvalCase],
    ) -> None:
        metrics = {**candidate.as_json(), "baseline": baseline.as_json()}
        decision = {"status": status, "improvement": improvement}
        report = {
            "cases": [
                {"case_id": str(case.id), "item_count": case.item_count} for case in cases
            ]
        }
        content_hash = hashlib.sha256(
            canonical_json({"metrics": metrics, "decision": decision, "report": report}).encode(
                "utf-8"
            )
        ).hexdigest()
        async with self._factory() as session:
            version = int(
                await session.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(EvalReport.version), 0)).where(
                        EvalReport.tenant_id == tenant_id,
                        EvalReport.task_family == task_family,
                        EvalReport.base_config_hash == base_config_hash,
                        (
                            EvalReport.experience_key.is_(None)
                            if experience_key is None
                            else EvalReport.experience_key == experience_key
                        ),
                    )
                )
                or 0
            ) + 1
            session.add(
                EvalReport(
                    tenant_id=tenant_id,
                    task_family=task_family,
                    experience_key=experience_key,
                    base_config_hash=base_config_hash,
                    metrics_json=metrics,
                    decision_json=decision,
                    report_json=report,
                    content_hash=content_hash,
                    version=version,
                )
            )
            await session.commit()
