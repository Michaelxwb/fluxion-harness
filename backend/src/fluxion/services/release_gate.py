"""ReleaseGateService：发布门禁（Phase 5 TASK-005）。

挂 publish 管道：候选版本 EvalRun 对比基线，score 回退超阈值 → 阻断发布
（S-06）；达标 → 放行（S-07，EvalRun 已留档 EvalRunStore）。复用
`EvaluationApplicationService.compare()`；blocked 决策含 score_delta 与原因。

- gate 等待超时 ≤2s（构造参数，fail-closed：超时阻断并记录，不无限等待）；
- 基线 run 不存在 → 阻断 + 明确错误「基线不可用」（E-04 / RISK-P5-04）；
- 阻断决策留档 AuditLog（规则 24，发布回滚复用既有治理）。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from fluxion.errors.console import RELEASE_GATE_BLOCKED, ConsoleError
from fluxion.registry import AuditRecord
from fluxion.services.eval_app import EvalTraceabilityError, EvaluationApplicationService


@dataclass(frozen=True, slots=True)
class GateDecision:
    """gate 决策（blocked 含 score_delta 与原因）。"""

    release_id: str
    tenant_id: str
    blocked: bool
    score_delta: float | None
    reason: str
    candidate_run_id: str | None
    baseline_run_id: str | None


class ConsoleReleaseGateBlockedError(ConsoleError):
    """publish 管道被 gate 阻断（envelope message 携带 score_delta 诊断）。"""

    def __init__(self, decision: GateDecision) -> None:
        delta = (
            f"{decision.score_delta:+.6f}" if decision.score_delta is not None else "N/A"
        )
        super().__init__(
            RELEASE_GATE_BLOCKED,
            (
                f"Release Gate 阻断: {decision.reason}（score_delta={delta}, "
                f"candidate={decision.candidate_run_id}, baseline={decision.baseline_run_id}）"
            ),
            409,
        )


class AuditSink(Protocol):
    async def append_audit(self, record: AuditRecord) -> None: ...


class ReleaseGateService:
    """发布门禁：evaluate(release_id, candidate, baseline, threshold) -> GateDecision。"""

    def __init__(
        self,
        evaluation: EvaluationApplicationService,
        *,
        audit_sink: AuditSink | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._evaluation = evaluation
        self._audit_sink = audit_sink
        self._timeout_seconds = timeout_seconds

    async def evaluate(
        self,
        *,
        release_id: str,
        tenant_id: str,
        candidate_eval_run_id: str,
        baseline_eval_run_id: str,
        threshold: float = 0.0,
        actor_id: str = "system",
        request_id: str = "req_unspecified",
        trace_id: str | None = None,
    ) -> GateDecision:
        """评估发布门禁；超时 fail-closed 阻断（有界，不阻塞 publish 主路径）。"""
        try:
            decision = await asyncio.wait_for(
                self._evaluate(
                    release_id=release_id,
                    tenant_id=tenant_id,
                    candidate_eval_run_id=candidate_eval_run_id,
                    baseline_eval_run_id=baseline_eval_run_id,
                    threshold=threshold,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            decision = GateDecision(
                release_id=release_id,
                tenant_id=tenant_id,
                blocked=True,
                score_delta=None,
                reason=f"gate 超时（fail-closed 阻断，>{self._timeout_seconds}s）",
                candidate_run_id=candidate_eval_run_id,
                baseline_run_id=baseline_eval_run_id,
            )
            del error
        if decision.blocked:
            await self._audit_block(decision, actor_id=actor_id, request_id=request_id, trace_id=trace_id)
        return decision

    async def _evaluate(
        self,
        *,
        release_id: str,
        tenant_id: str,
        candidate_eval_run_id: str,
        baseline_eval_run_id: str,
        threshold: float,
    ) -> GateDecision:
        # E-04：基线不可用 → 阻断 + 明确错误（提示重跑基线）
        baseline = await self._evaluation.get_run(baseline_eval_run_id, tenant_id=tenant_id)
        if baseline is None:
            return GateDecision(
                release_id=release_id,
                tenant_id=tenant_id,
                blocked=True,
                score_delta=None,
                reason="基线不可用（EvalRun 不存在，请重跑基线）",
                candidate_run_id=candidate_eval_run_id,
                baseline_run_id=baseline_eval_run_id,
            )
        # 复用 compare()：候选 run 不可用 → EvalTraceabilityError → 阻断
        try:
            regression = await self._evaluation.compare(
                tenant_id=tenant_id,
                run_id=candidate_eval_run_id,
                baseline_run_id=baseline_eval_run_id,
            )
        except EvalTraceabilityError as error:
            return GateDecision(
                release_id=release_id,
                tenant_id=tenant_id,
                blocked=True,
                score_delta=None,
                reason=f"候选 EvalRun 不可用: {error}",
                candidate_run_id=candidate_eval_run_id,
                baseline_run_id=baseline_eval_run_id,
            )
        delta = regression.score_delta
        if delta < -threshold:
            return GateDecision(
                release_id=release_id,
                tenant_id=tenant_id,
                blocked=True,
                score_delta=delta,
                reason=f"score 回退 {delta:+.6f} 超出阈值 {threshold}（score_delta 回退阻断）",
                candidate_run_id=candidate_eval_run_id,
                baseline_run_id=baseline_eval_run_id,
            )
        return GateDecision(
            release_id=release_id,
            tenant_id=tenant_id,
            blocked=False,
            score_delta=delta,
            reason=f"达标（score_delta={delta:+.6f} ≥ -{threshold}）",
            candidate_run_id=candidate_eval_run_id,
            baseline_run_id=baseline_eval_run_id,
        )

    async def _audit_block(
        self,
        decision: GateDecision,
        *,
        actor_id: str,
        request_id: str,
        trace_id: str | None,
    ) -> None:
        """阻断决策留档 AuditLog（规则 24；发布回滚复用既有治理）。"""
        if self._audit_sink is None:
            return
        await self._audit_sink.append_audit(
            AuditRecord(
                audit_id=uuid.uuid4().hex,
                tenant_id=decision.tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                action="release_gate.blocked",
                target_type="release",
                target_id=decision.release_id,
                before=None,
                after={
                    "reason": decision.reason,
                    "score_delta": decision.score_delta,
                    "candidate_run_id": decision.candidate_run_id,
                    "baseline_run_id": decision.baseline_run_id,
                },
                trace_id=trace_id,
                created_at=datetime.now(UTC),
            )
        )
