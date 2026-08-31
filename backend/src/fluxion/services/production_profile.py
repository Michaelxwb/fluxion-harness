"""Production profile 装配守卫（Phase 6 TASK-006 ⑤，承接 FEAT-P6-05 ④ / P0-5）。

RULE-P6-05 fail-closed：production profile 下 InMemory Secret/Approval/Eval/
Trace 作为唯一实现 → 启动明确拒绝（明确错误，不静默降级）。

- 判定基于 Adapter 显式 capability 声明（白名单，TASK-013）——替代 isinstance
  InMemory 黑名单；未知/未声明 adapter fail-closed，可扩展不误伤；
- 违规项一次性全部列出（运维一次修复，不逐个撞墙）。
"""

from __future__ import annotations

REQUIRED_PRODUCTION_CAPABILITIES = frozenset({"durability", "multi_replica"})


class ProductionProfileError(RuntimeError):
    """production profile 装配违规（fail-fast，启动拒绝）。"""

    code = "production_profile_violation"


def _declared_capabilities(store: object) -> frozenset[str]:
    caps = getattr(store, "production_capabilities", frozenset())
    return frozenset(caps)


def verify_production_assembly(
    *,
    secret_store: object,
    trace_store: object,
    approval_store: object,
    eval_run_store: object,
) -> None:
    """校验 production 装配各 store 显式声明 production capability；违规 → ProductionProfileError。

    白名单语义：只有显式声明 durability/multi-replica 的 adapter 才放行；
    InMemory/Local 未声明 → 缺失能力 → fail-closed。
    """
    violations: list[str] = []
    for label, store in (
        ("secret store", secret_store),
        ("trace store", trace_store),
        ("approval store", approval_store),
        ("eval run store", eval_run_store),
    ):
        missing = REQUIRED_PRODUCTION_CAPABILITIES - _declared_capabilities(store)
        if missing:
            violations.append(
                f"{label} 缺少 production capability {sorted(missing)}——"
                "production 须装配显式声明 durability/multi-replica 的 adapter"
            )
    if violations:
        raise ProductionProfileError(
            "production profile 装配违规（P0-5 fail-fast，不静默降级）：\n- "
            + "\n- ".join(violations)
        )


__all__ = ["REQUIRED_PRODUCTION_CAPABILITIES", "ProductionProfileError", "verify_production_assembly"]
