"""Redis primitives for shared egress admission.

This module is not wired into a request path. A deployment must first provide an
externally managed epoch and execution fencing before enabling shared metering.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Protocol

from muad_contracts.shared_egress import SharedEgressPolicy, SharedRule, TrustedInvocation
from redis.exceptions import RedisClusterException, RedisError

from .shared_redis_scripts import ADMIT, CONSUME, PUBLISH, REGISTER_SCOPE, RELEASE, RENEW


class RedisCommands(Protocol):
    async def execute_command(self, *args: str | int) -> object: ...


class MeteringUnavailable(RuntimeError):
    """Metering state cannot safely authorize an outbound request."""


class SharedLimitExceeded(RuntimeError):
    def __init__(self, kind: str, rule_id: str, detail: int) -> None:
        self.kind, self.rule_id, self.detail = kind, rule_id, detail
        super().__init__(f"shared egress {kind.lower()} limit exceeded")


@dataclass(frozen=True, slots=True)
class SharedPermit:
    attempt_id: str
    proof: str
    expires_at_ms: int
    rule_ids: tuple[str, ...]  # Only rules holding inflight leases.


class SharedRedisMeter:
    def __init__(self, redis: RedisCommands, *, domain: str, epoch: str) -> None:
        if not domain or any(c in domain for c in "{}") or not epoch:
            raise ValueError("a valid protection domain and epoch are required")
        self._redis = redis
        self._prefix = f"fx:egress:{{{domain}}}:"
        self._epoch = epoch

    @property
    def epoch(self) -> str:
        return self._epoch

    def _key(self, suffix: str) -> str:
        return self._prefix + suffix

    def _bucket(self, rule_id: str, kind: str) -> str:
        digest = hashlib.sha256(rule_id.encode()).hexdigest()
        return self._key(f"rule:{digest}:{kind}")

    def _scope(self, kind: str, execution_id: str, invocation_id: str = "") -> tuple[str, str, str]:
        if not execution_id.strip() or (kind == "invocation" and not invocation_id.strip()):
            raise ValueError("scope identity must not be blank")
        identity = json.dumps([kind, execution_id, invocation_id], ensure_ascii=False, separators=(",", ":"))
        name = hashlib.sha256(identity.encode()).hexdigest()
        return name, identity, self._key(f"scope:{name}")

    async def _register_scope(
        self, scope: tuple[str, str, str], max_requests: int | None,
        parent: tuple[str, str, str] | None = None, *, existing_only: bool = False,
        targets: tuple[SharedRule, ...] = (),
    ) -> None:
        if max_requests is not None and (
            isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0
        ):
            raise ValueError("max_requests must be a positive integer or None")
        name, identity, key = scope
        parent_name, parent_identity, parent_key = parent or ("", "", self._key("guard"))
        result = await self._eval(REGISTER_SCOPE, [
            self._key("guard"), self._key("scope-index"), key, parent_key,
        ], [self._epoch, name, identity, str(max_requests) if max_requests is not None else "none",
            parent_identity, parent_name, "resume" if existing_only else "create",
            self._frozen_targets_json(targets)])
        if result[0] != "OK":
            raise MeteringUnavailable(f"shared scope registration failed: {result[0]}")

    @staticmethod
    def _frozen_targets_json(targets: tuple[SharedRule, ...]) -> str:
        if len({rule.id for rule in targets}) != len(targets):
            raise ValueError("duplicate frozen target rule")
        for rule in targets:
            if rule.max_requests is None or any(value is not None for value in (
                rule.window_requests, rule.window_seconds, rule.max_inflight,
            )):
                raise ValueError("frozen targets currently require cumulative limits only")
        return json.dumps([rule.model_dump(mode="json") for rule in targets],
                          sort_keys=True, separators=(",", ":"))

    async def register_execution(
        self, execution_id: str, *, max_requests: int | None, targets: tuple[SharedRule, ...] = (),
    ) -> None:
        """Initialize a host-authorized new execution; not an authority or recovery check."""
        await self._register_scope(self._scope("execution", execution_id), max_requests, targets=targets)

    async def register_invocation(
        self, execution_id: str, invocation_id: str, *, max_requests: int | None,
        targets: tuple[SharedRule, ...] = (),
    ) -> None:
        """Initialize a host-authorized new invocation under an existing execution."""
        await self._register_scope(
            self._scope("invocation", execution_id, invocation_id), max_requests,
            self._scope("execution", execution_id), targets=targets,
        )

    async def resume_execution(
        self, execution_id: str, *, max_requests: int | None, targets: tuple[SharedRule, ...] = (),
    ) -> None:
        """Validate an existing host-owned scope without initializing missing state."""
        await self._register_scope(self._scope("execution", execution_id), max_requests,
                                   existing_only=True, targets=targets)

    async def resume_invocation(
        self, execution_id: str, invocation_id: str, *, max_requests: int | None,
        targets: tuple[SharedRule, ...] = (),
    ) -> None:
        """Validate existing invocation state; the host must authenticate ownership."""
        await self._register_scope(
            self._scope("invocation", execution_id, invocation_id), max_requests,
            self._scope("execution", execution_id), existing_only=True, targets=targets,
        )

    async def _eval(self, script: str, keys: list[str], args: list[str]) -> list[str]:
        try:
            result = await self._redis.execute_command("EVAL", script, len(keys), *keys, *args)
        except (RedisError, RedisClusterException) as exc:
            raise MeteringUnavailable("shared metering unavailable") from exc
        if not isinstance(result, (list, tuple)) or not result:
            raise MeteringUnavailable("invalid metering response")
        return [part.decode() if isinstance(part, bytes) else str(part) for part in result]

    @staticmethod
    def _policy_json(policy: SharedEgressPolicy) -> str:
        return json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    async def publish(self, policy: SharedEgressPolicy) -> None:
        """Publish only into an already-provisioned, externally guarded epoch."""
        keys = [self._key("guard"), self._key("policy"), self._key("decisions")]
        for rule in policy.rules:
            keys.extend(self._bucket(rule.id, kind) for kind in ("total", "window", "leases"))
        args = [self._epoch, str(policy.revision), self._policy_json(policy)]
        result = await self._eval(PUBLISH, keys, args)
        if result[0] != "OK":
            raise MeteringUnavailable(f"shared policy publication failed: {result[0]}")

    async def admit(
        self, policy: SharedEgressPolicy, *, target_instance_id: str, route_id: str,
        attempt_id: str, lease_seconds: float, invocation: TrustedInvocation | None = None,
    ) -> SharedPermit | None:
        if (not attempt_id or attempt_id.startswith("__") or not math.isfinite(lease_seconds)
                or lease_seconds <= 0):
            raise ValueError("a unique attempt and a finite positive lease are required")
        rules = tuple(r for r in policy.rules
                      if r.target_instance_id == target_instance_id and r.route_id == route_id)
        if not policy.enabled and invocation is None:
            return None
        scopes = () if invocation is None else (
            self._scope("execution", invocation.execution_id),
            self._scope("invocation", invocation.execution_id, invocation.invocation_id),
        )
        request_identity = json.dumps(
            [self._epoch, attempt_id, target_instance_id, route_id,
             self._policy_json(policy), math.ceil(lease_seconds * 1000),
             [*scopes], invocation.segment_id if invocation is not None else None],
            ensure_ascii=False, separators=(",", ":"),
        )
        proof = hashlib.sha256(request_identity.encode()).hexdigest()
        keys = [self._key("guard"), self._key("policy"), self._key("decisions")]
        for rule in rules:
            keys.extend(self._bucket(rule.id, kind) for kind in ("total", "window", "leases"))
        if scopes:
            keys.append(self._key("scope-index"))
            keys.extend(scope[2] for scope in scopes)
        result = await self._eval(ADMIT, keys, [
            self._epoch, self._policy_json(policy), attempt_id, proof,
            json.dumps([r.model_dump(mode="json") for r in rules], separators=(",", ":")),
            str(math.ceil(lease_seconds * 1000)),
            json.dumps([{"name": name, "identity": identity} for name, identity, _ in scopes],
                       separators=(",", ":")),
            target_instance_id, route_id,
        ])
        if result[0] == "UNCONTROLLED" and not rules:
            return None
        return self._permit(result, attempt_id, proof, rules)

    @staticmethod
    def _permit(
        result: list[str], attempt_id: str, proof: str, rules: tuple[SharedRule, ...],
    ) -> SharedPermit:
        if result[0] in {"TOTAL", "WINDOW", "INFLIGHT"} and len(result) == 3:
            raise SharedLimitExceeded(result[0], result[1], int(result[2]))
        if result[0] not in {"OK", "DUPLICATE"} or len(result) != 2:
            raise MeteringUnavailable(f"shared admission failed: {result[0]}")
        inflight_ids = tuple(r.id for r in rules if r.max_inflight is not None)
        return SharedPermit(attempt_id, proof, int(result[1]), inflight_ids)

    async def release(self, permit: SharedPermit) -> None:
        keys = [self._key("decisions")]
        keys.extend(self._bucket(rule_id, "leases") for rule_id in permit.rule_ids)
        result = await self._eval(RELEASE, keys, [permit.attempt_id, permit.proof])
        if result[0] != "OK":
            raise MeteringUnavailable(f"shared lease release failed: {result[0]}")

    async def consume(self, permit: SharedPermit) -> None:
        """Claim a permit once before sending; an uncertain claim must not send."""
        keys = [self._key("guard"), self._key("decisions")]
        result = await self._eval(CONSUME, keys, [self._epoch, permit.attempt_id, permit.proof])
        if result[0] != "OK":
            raise MeteringUnavailable(f"shared permit consumption failed: {result[0]}")

    async def renew(self, permit: SharedPermit, *, lease_seconds: float) -> None:
        """Extend a consumed lease; the host must supervise transport and deadline."""
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        result = await self._eval(RENEW, [
            self._key("decisions"), self._key("guard"),
            *(self._bucket(rule_id, "leases") for rule_id in permit.rule_ids),
        ], [permit.attempt_id, permit.proof, str(math.ceil(lease_seconds * 1000)), self._epoch])
        if result[0] != "OK":
            raise MeteringUnavailable(f"shared lease renewal failed: {result[0]}")
