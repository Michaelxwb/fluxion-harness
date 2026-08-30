"""SurfaceEvidence 证据驱动表面判定（Phase 6 TASK-003 / FEAT-P6-03，remediation §17.1）。

客观证据字段（无主观判断）+ 三级分类：

- ``EXTERNAL_ACTIVE``：任一证据命中（活跃记录/有效 token/启用集成/近 30 天流量/
  已知外部消费方/公开稳定契约）→ 只可 Rollover 双写，禁止直接 reset；
- ``RESET_ALLOWED``：全部证据为零且无外部消费 → 直接 reset，不建双写；
- ``UNKNOWN``：证据不足（字段缺失/无法确认）→ **按 EXTERNAL_ACTIVE 处理，禁止
  destructive reset**（RULE-P6-03 保守默认）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SurfaceEvidence:
    """客观证据字段（design §2.3.2；None = 缺失/无法确认 → 判 UNKNOWN）。"""

    active_record_count: int | None
    active_token_count: int | None
    enabled_integration_count: int | None
    traffic_30d: int | None
    last_used_at: datetime | None
    known_external_consumer: bool | None
    public_stable_contract: bool | None
    evidence_source: str


class SurfaceClassification(StrEnum):
    EXTERNAL_ACTIVE = "external_active"
    RESET_ALLOWED = "reset_allowed"
    UNKNOWN = "unknown"


def classify_surface(evidence: SurfaceEvidence) -> SurfaceClassification:
    """三级分类（客观字段驱动；UNKNOWN 保守默认）。"""
    # 证据不足：任何计数/布尔字段缺失（None）且无法确认 → UNKNOWN
    #（last_used_at 为 None 语义是「从未使用」，非证据缺失）
    if (
        evidence.active_record_count is None
        or evidence.active_token_count is None
        or evidence.enabled_integration_count is None
        or evidence.traffic_30d is None
        or evidence.known_external_consumer is None
        or evidence.public_stable_contract is None
    ):
        return SurfaceClassification.UNKNOWN
    # 任一证据命中 → EXTERNAL_ACTIVE
    if (
        evidence.active_record_count > 0
        or evidence.active_token_count > 0
        or evidence.enabled_integration_count > 0
        or evidence.traffic_30d > 0
        or evidence.known_external_consumer
        or evidence.public_stable_contract
        or evidence.last_used_at is not None
    ):
        return SurfaceClassification.EXTERNAL_ACTIVE
    # 全部为零且无外部消费 → RESET_ALLOWED
    return SurfaceClassification.RESET_ALLOWED


__all__ = ["SurfaceClassification", "SurfaceEvidence", "classify_surface"]
