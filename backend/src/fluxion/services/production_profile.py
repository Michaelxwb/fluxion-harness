"""Production profile 装配守卫（Phase 6 TASK-006 ⑤，承接 FEAT-P6-05 ④ / P0-5）。

RULE-P6-05 fail-closed：production profile 下 InMemory Secret/Approval/Eval/
Trace 作为唯一实现 → 启动明确拒绝（明确错误，不静默降级）。

- 判定基于装配类型（isinstance），由 composition root 在启动时调用；
- 违规项一次性全部列出（运维一次修复，不逐个撞墙）；
- E-08 的 RuntimeScheduler 本地实现守卫由 FEAT-P6-05 ⑤（TASK-005）在同一
  装配路径上扩展。
"""

from __future__ import annotations

from fluxion.runtime.secrets import LocalEncryptedSecretStore
from fluxion.runtime.tracing import InMemoryTraceStore
from fluxion.services.approval_app import InMemoryApprovalStore
from fluxion.services.eval_app import InMemoryEvalRunStore


class ProductionProfileError(RuntimeError):
    """production profile 装配违规（fail-fast，启动拒绝）。"""

    code = "production_profile_violation"


def verify_production_assembly(
    *,
    secret_store: object,
    trace_store: object,
    approval_store: object,
    eval_run_store: object,
) -> None:
    """校验 production 装配不含 InMemory 唯一实现；违规 → ProductionProfileError。

    全部通过 → 静默返回（fail-open 于显式 production adapter，fail-closed 于
    InMemory/Local 实现）。
    """
    violations: list[str] = []
    if isinstance(secret_store, LocalEncryptedSecretStore):
        violations.append(
            "secret store 是 LocalEncryptedSecretStore（内存密文）——"
            "production 须装配 PostgresEncryptedSecretStore"
        )
    if isinstance(trace_store, InMemoryTraceStore):
        violations.append(
            "trace store 是 InMemoryTraceStore——production 须装配 PostgresTraceStore"
        )
    if isinstance(approval_store, InMemoryApprovalStore):
        violations.append(
            "approval store 是 InMemoryApprovalStore——"
            "production 须装配 PostgresApprovalStore"
        )
    if isinstance(eval_run_store, InMemoryEvalRunStore):
        violations.append(
            "eval run store 是 InMemoryEvalRunStore——"
            "production 须装配 PostgresEvalRunStore"
        )
    if violations:
        raise ProductionProfileError(
            "production profile 装配违规（P0-5 fail-fast，不静默降级）：\n- "
            + "\n- ".join(violations)
        )


__all__ = ["ProductionProfileError", "verify_production_assembly"]
