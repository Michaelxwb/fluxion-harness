"""TASK-006（Phase 6）production profile fail-fast 守卫（FEAT-P6-06 ⑤，承接
FEAT-P6-05 ④ / P0-5 / REQ-OBS-002 / REQ-SEC-006）。

S-10 断言「production InMemory fail-fast」：production profile 下 InMemory
Secret/Approval/Eval/Trace 作为唯一实现 → 启动明确拒绝，不静默降级。

真实边界：真实类装配路径（直接构造 InMemory/Local 与 durable store 实例，
验证 verify_production_assembly 判定），无 HTTP mock。
"""

from __future__ import annotations

import pytest

from fluxion.runtime.secrets import LocalEncryptedSecretStore
from fluxion.runtime.tracing import InMemoryTraceStore
from fluxion.services.approval_app import InMemoryApprovalStore
from fluxion.services.eval_app import InMemoryEvalRunStore
from fluxion.services.production_profile import (
    ProductionProfileError,
    verify_production_assembly,
)


def _inmemory_stack() -> dict[str, object]:
    return {
        "secret_store": LocalEncryptedSecretStore(master_key=b"k" * 32),
        "trace_store": InMemoryTraceStore(),
        "approval_store": InMemoryApprovalStore(),
        "eval_run_store": InMemoryEvalRunStore(),
    }


def _durable_stack() -> dict[str, object]:
    """durable 替身：类型非 InMemory/Local 即视为显式 production adapter。

    真实 durable 实现的装配行为由 test_production_assembly.py /
    test_durable_stores.py 以真实 PG 验证；此处只测守卫的类型判定逻辑。
    """

    class _DurableTrace:
        pass

    class _DurableApproval:
        pass

    class _DurableEvalRun:
        pass

    class _DurableSecret:
        pass

    return {
        "secret_store": _DurableSecret(),
        "trace_store": _DurableTrace(),
        "approval_store": _DurableApproval(),
        "eval_run_store": _DurableEvalRun(),
    }


class TestProductionProfileGuard:
    def test_inmemory_only_assembly_fail_fast(self) -> None:
        """E-07/P0-5：四个 store 全 InMemory → 明确拒绝并列出全部违规项。"""
        with pytest.raises(ProductionProfileError) as excinfo:
            verify_production_assembly(**_inmemory_stack())
        message = str(excinfo.value)
        assert "trace" in message
        assert "approval" in message
        assert "eval" in message
        assert "secret" in message

    def test_partial_inmemory_fail_fast(self) -> None:
        """部分 InMemory（如仅 Trace）→ 同样拒绝（唯一实现即违规）。"""
        stack = _durable_stack()
        stack["trace_store"] = InMemoryTraceStore()
        with pytest.raises(ProductionProfileError, match="trace"):
            verify_production_assembly(**stack)

    def test_local_secret_store_fail_fast(self) -> None:
        """LocalEncryptedSecretStore（内存密文）在 production 同样拒绝。"""
        stack = _durable_stack()
        stack["secret_store"] = LocalEncryptedSecretStore(master_key=b"k" * 32)
        with pytest.raises(ProductionProfileError, match="secret"):
            verify_production_assembly(**stack)

    def test_durable_assembly_passes(self) -> None:
        """全部显式 production adapter → 守卫放行（不抛异常）。"""
        verify_production_assembly(**_durable_stack())
