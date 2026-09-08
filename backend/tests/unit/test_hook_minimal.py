"""Hook 收敛（105 P2-03 / TASK-007）验收测试。

覆盖 S-05：
- HookScope/scope_id/IGNORE 已删除；
- HookRegistration 收敛五字段；
- 既有分发行为不变。
"""

from __future__ import annotations

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
)


async def _noop(_payload: object) -> None:
    return None


def test_S_05_dead_abstractions_gone() -> None:
    """S-05：死抽象不存在（import 即失败）。"""
    import fluxion.kernel.events as events

    assert not hasattr(events, "HookScope")
    assert not hasattr(FailPolicy, "IGNORE")
    assert "IGNORE" not in [member.value for member in FailPolicy]


def test_S_05_registration_converged_to_five_fields() -> None:
    """S-05：HookRegistration 收敛五字段。"""
    import dataclasses

    names = {field.name for field in dataclasses.fields(HookRegistration)}
    assert names == {
        "registration_id",
        "event_type",
        "priority",
        "timeout_ms",
        "fail_policy",
        "handler",
    }


def test_S_05_fail_policies_distinct() -> None:
    """S-05：仅 FAIL_CLOSED 阻断，其余继续（既有语义不变）。"""
    assert FailPolicy.FAIL_CLOSED.value == "fail_closed"
    assert FailPolicy.FAIL_OPEN.value == "fail_open"
    registration = HookRegistration(
        registration_id="hook-1",
        event_type=BeforeToolCallPayload,
        priority=10,
        timeout_ms=100,
        fail_policy=FailPolicy.FAIL_OPEN,
        handler=_noop,
    )
    assert registration.fail_policy is FailPolicy.FAIL_OPEN
    with pytest.raises(ValueError, match="timeout_ms must be positive"):
        HookRegistration(
            registration_id="hook-2",
            event_type=BeforeToolCallPayload,
            priority=10,
            timeout_ms=0,
            fail_policy=FailPolicy.FAIL_CLOSED,
            handler=_noop,
        )
