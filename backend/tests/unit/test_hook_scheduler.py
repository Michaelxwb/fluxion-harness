from __future__ import annotations

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    HookScheduler,
    HookScope,
)


async def _noop(_payload: BeforeToolCallPayload) -> None:
    return None


def test_B_R02_same_priority_hook_order_is_stable() -> None:
    scheduler = HookScheduler()
    for index in range(6):
        scheduler.register(
            HookRegistration(
                registration_id=f"hook-{index}",
                event_type=BeforeToolCallPayload,
                priority=10,
                timeout_ms=100,
                fail_policy=FailPolicy.FAIL_CLOSED,
                scope=HookScope.GLOBAL,
                handler=_noop,
            )
        )

    expected = [f"hook-{index}" for index in range(6)]
    for _ in range(20):
        ordered = scheduler.ordered(BeforeToolCallPayload)
        assert [registration.registration_id for registration in ordered] == expected
