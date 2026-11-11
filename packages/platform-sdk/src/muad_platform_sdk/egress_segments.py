"""Host-only execution segments; PG/source lease authentication belongs to callers.

Activation requires a PG-authorized monotone fence. It does not acquire a Task
or Run lease. A stop confirmation is legal only after process/HTTP termination.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation

from .egress_segment_scripts import FENCED_OPERATIONS, SEGMENT_CONTROL
from .shared_redis import MeteringUnavailable, RedisCommands, SharedPermit, SharedRedisMeter


@dataclass(frozen=True, slots=True)
class SegmentGrant:
    execution_id: str
    fence: int
    segment_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if (not self.execution_id.strip() or not self.segment_id.strip() or
                isinstance(self.fence, bool) or not isinstance(self.fence, int) or
                self.fence <= 0 or self.fence >= 2**53 - 1):
            raise ValueError("stable execution, positive fence and unique segment are required")


class FencedRedisMeter(SharedRedisMeter):
    def __init__(self, redis: RedisCommands, *, domain: str, epoch: str, grant: SegmentGrant) -> None:
        super().__init__(redis, domain=domain, epoch=epoch)
        self._grant = grant
        self._scope_name, self._identity, _ = self._scope("execution", grant.execution_id)
        self.segment_key = self._key("segment:" + self._scope_name)

    @property
    def grant(self) -> SegmentGrant:
        return self._grant

    async def _eval(self, script: str, keys: list[str], args: list[str]) -> list[str]:
        guarded = FENCED_OPERATIONS.get(script)
        if guarded is None:
            return await super()._eval(script, keys, args)
        return await super()._eval(
            guarded, [*keys, self._key("guard"), self.segment_key,
                      self._key("scope-index"), self._scope("execution", self.grant.execution_id)[2]],
            [*args, self.epoch, str(self.grant.fence), self.grant.segment_id,
             self._identity, self._scope_name],
        )

    async def _control(self, mode: str, lease_ms: int, margin_ms: int) -> None:
        result = await super()._eval(SEGMENT_CONTROL, [
            self._key("guard"), self._scope("execution", self.grant.execution_id)[2],
            self._key("scope-index"), self.segment_key,
        ], [self.epoch, self._identity, self._scope_name, str(self.grant.fence),
            self.grant.segment_id, str(lease_ms), str(margin_ms), mode])
        if result != ["OK"]:
            raise MeteringUnavailable(f"execution segment {mode} failed: {result[0]}")

    async def activate(self, *, lease_seconds: float, stop_margin_seconds: float) -> None:
        """Activate/renew only with currently valid PG/source authority.

        No automatic resurrection or takeover while the previous segment may
        still own a socket. The host must stop locally before its lease expires.
        """
        if any(not math.isfinite(v) or v <= 0 for v in (lease_seconds, stop_margin_seconds)):
            raise ValueError("positive finite segment lease and stop margin required")
        await self._control("activate", math.ceil(lease_seconds * 1000),
                            math.ceil(stop_margin_seconds * 1000))

    async def confirm_stopped(self) -> None:
        """Only after confirmed child-group/HTTP termination; stale fences fail."""
        await self._control("stopped", 0, 0)

    async def admit(
        self, policy: SharedEgressPolicy, *, target_instance_id: str, route_id: str,
        attempt_id: str, lease_seconds: float, invocation: TrustedInvocation | None = None,
    ) -> SharedPermit | None:
        if (invocation is None or invocation.execution_id != self.grant.execution_id or
                invocation.segment_id != self.grant.segment_id):
            raise MeteringUnavailable("request is outside its bound execution segment")
        return await super().admit(
            policy, target_instance_id=target_instance_id, route_id=route_id,
            attempt_id=attempt_id, lease_seconds=lease_seconds, invocation=invocation,
        )
