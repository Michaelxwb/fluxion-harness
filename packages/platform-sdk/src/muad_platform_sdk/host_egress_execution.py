"""Embedded host assembly: PG identity, segment supervision and physical clients.

Apps own source readers, trusted route configuration and the Redis connection.
This object does not create a service, provision epochs, publish public rules,
or infer execution ownership from script input. A child closure receipt must
come from the actual sandbox executor before an execution may be handed over.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from muad_contracts.shared_egress import FrozenIdentityBudgets, TrustedInvocation
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_attempt_audit import AttemptObserver
from .egress_identity_registry import (
    ExecutionRegistration,
    IdentityRegistry,
    IdentityUnavailable,
    ScopeIdentity,
)
from .egress_ipc import AttemptMetadata, EgressTarget, InvocationEgressBroker, UncontrolledTarget
from .egress_segment_supervisor import SourceSegmentSupervisor
from .egress_segments import FencedRedisMeter
from .egress_source_authority import PGSegmentAuthority, SourceClaim, SourceReader
from .persistent_egress import PersistentEgressScopes
from .shared_host_transport import HostRoute, SharedHostTransport
from .shared_http_transport import RequestLifetime
from .shared_redis import RedisCommands, SharedLimitExceeded, SharedRedisMeter

TargetResolver = Callable[
    [AttemptMetadata],
    EgressTarget | UncontrolledTarget | None | Awaitable[EgressTarget | UncontrolledTarget | None],
]


@dataclass(frozen=True, slots=True)
class HostEgressSettings:
    domain: str
    epoch: str
    public_rules_enabled: bool
    lifetime: RequestLifetime
    source_refresh_seconds: float
    stop_margin_seconds: float

    def __post_init__(self) -> None:
        if not self.domain or any(c in self.domain for c in "{}") or not self.epoch:
            raise ValueError("provisioned protection domain and epoch required")
        if not isinstance(self.public_rules_enabled, bool):
            raise ValueError("explicit public control mode required")
        if any(
            isinstance(v, bool) or not math.isfinite(v) or v <= 0
            for v in (
                self.source_refresh_seconds,
                self.stop_margin_seconds,
            )
        ):
            raise ValueError("positive finite supervision intervals required")


class HostEgressExecution:
    def __init__(
        self,
        *,
        settings: HostEgressSettings,
        scope: ScopeIdentity,
        authority: PGSegmentAuthority,
        claim: SourceClaim,
        reader: SourceReader,
        meter: FencedRedisMeter,
        registry: IdentityRegistry,
        resolve: TargetResolver,
        observe: AttemptObserver | None = None,
    ) -> None:
        self._settings, self._scope, self._authority = settings, scope, authority
        self._claim, self._reader, self._meter = claim, reader, meter
        self._scopes, self._resolve = PersistentEgressScopes(registry, meter), resolve
        self._observe = observe
        self._brokers: list[InvocationEgressBroker] = []
        self._transports: list[SharedHostTransport] = []
        self._close_lock, self._closed = asyncio.Lock(), asyncio.Event()
        self._closing, self._started = False, False

    @classmethod
    async def create(
        cls,
        *,
        settings: HostEgressSettings,
        budget: FrozenIdentityBudgets,
        sessions: async_sessionmaker[AsyncSession],
        redis: RedisCommands,
        claim: SourceClaim,
        reader: SourceReader,
        resolve: TargetResolver,
        observe: AttemptObserver | None = None,
    ) -> HostEgressExecution | None:
        if not settings.public_rules_enabled and not budget.enabled:
            return None  # No PG/Redis calls or registration on the unlimited path.
        registry = IdentityRegistry(sessions)
        base = SharedRedisMeter(redis, domain=settings.domain, epoch=settings.epoch)
        await base.current_policy()  # Check provisioning before claiming PG initialization.
        scope = await PersistentEgressScopes(registry, base).execution(
            ExecutionRegistration(
                tenant_id=claim.tenant_id,
                source_kind=claim.source_kind,
                source_id=claim.source_id,
                meter_epoch=settings.epoch,
                budget=budget,
            )
        )
        authority = PGSegmentAuthority(sessions, meter_epoch=settings.epoch)
        authorization = await authority.authorize(scope.id, claim, reader)
        meter = FencedRedisMeter(
            redis, domain=settings.domain, epoch=settings.epoch, grant=authorization.grant
        )
        return cls(
            settings=settings,
            scope=scope,
            authority=authority,
            claim=claim,
            reader=reader,
            meter=meter,
            registry=registry,
            resolve=resolve,
            observe=observe,
        )

    def _require_open(self) -> None:
        if self._closing:
            raise IdentityUnavailable("host execution is closing")

    async def host_invocation(self, *, operation_key: str, skill_key: str) -> TrustedInvocation:
        self._require_open()
        authorized = await self._authority.authorize(self._scope.id, self._claim, self._reader)
        if authorized.grant != self._meter.grant:
            raise IdentityUnavailable("source segment changed")
        scope = await self._scopes.invocation(
            tenant_id=self._claim.tenant_id,
            execution_id=self._scope.id,
            operation_key=operation_key,
            skill_key=skill_key,
        )
        self._require_open()
        return TrustedInvocation(str(self._scope.id), str(scope.id), self._meter.grant.segment_id)

    def broker(self, invocation: TrustedInvocation, *, deadline_seconds: float) -> InvocationEgressBroker:
        self._require_open()
        self._validate_invocation(invocation)
        broker = InvocationEgressBroker(
            meter=self._meter,
            invocation=invocation,
            resolve=self._resolve,
            deadline_seconds=deadline_seconds,
            cleanup_seconds=self._settings.lifetime.cleanup_seconds,
            renewal_interval_seconds=self._settings.lifetime.renewal_interval_seconds,
            observe=self._observe,
        )
        self._brokers.append(broker)
        return broker

    def _validate_invocation(self, invocation: TrustedInvocation) -> None:
        if (
            invocation.execution_id != str(self._scope.id)
            or invocation.segment_id != self._meter.grant.segment_id
        ):
            raise IdentityUnavailable("invocation belongs to another execution segment")

    async def _host_route(self, request: httpx.Request) -> HostRoute | None:
        origin = str(request.url.copy_with(path="/", query=None)).rstrip("/")
        resolved = self._resolve(
            AttemptMetadata(
                method=request.method,
                origin=origin,
                path=request.url.raw_path.split(b"?", 1)[0].decode("ascii"),
                authority=request.headers.get("host"),
            )
        )
        target = await resolved if inspect.isawaitable(resolved) else resolved
        if not isinstance(target, EgressTarget):
            return None
        return HostRoute(target.target_instance_id, target.route_id, target.policy)

    def transport(
        self,
        invocation: TrustedInvocation,
        *,
        verify: ssl.SSLContext | bool = True,
        on_limit: Callable[[SharedLimitExceeded], None] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> SharedHostTransport:
        self._require_open()
        self._validate_invocation(invocation)
        transport = SharedHostTransport(
            self._meter,
            public_rules_enabled=self._settings.public_rules_enabled,
            resolve=self._host_route,
            lifetime=self._settings.lifetime,
            invocation=invocation,
            verify=verify,
            on_limit=on_limit,
            client=client,
            observe=self._observe,
        )
        self._transports.append(transport)
        return transport

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        self._require_open()
        if self._started:
            raise IdentityUnavailable("host execution is single-use")
        self._started = True
        supervisor = SourceSegmentSupervisor(
            authority=self._authority,
            execution_id=self._scope.id,
            claim=self._claim,
            reader=self._reader,
            meter=self._meter,
            refresh_seconds=self._settings.source_refresh_seconds,
            stop_margin_seconds=self._settings.stop_margin_seconds,
            close_clients=self.aclose,
        )
        return await supervisor.run(operation)

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed.is_set():
                return
            self._closing = True
            outcomes = await asyncio.gather(
                *(b.stop_admissions() for b in self._brokers),
                *(t.aclose() for t in self._transports),
                return_exceptions=True,
            )
            if any(isinstance(value, BaseException) for value in outcomes):
                raise IdentityUnavailable("host client termination not confirmed")
            await asyncio.gather(*(b.confirm_idle() for b in self._brokers))
            await asyncio.gather(*(b.clients_closed.wait() for b in self._brokers))
            self._closed.set()
