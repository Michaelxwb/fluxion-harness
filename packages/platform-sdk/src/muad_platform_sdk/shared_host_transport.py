"""Host-owned HTTPX physical-send boundary for Adapter and MCP clients.

The pump owns the existing streaming lifecycle in one task. A bounded relay
preserves HTTPX streaming and cancellation without transferring timeout contexts
between consumer tasks. No service, Redis credential, or identity enters Skill.
"""

from __future__ import annotations

import asyncio
import inspect
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation

from .egress_attempt_audit import AttemptObserver
from .shared_http_transport import RequestLifetime, SharedHTTPTransport
from .shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter


@dataclass(frozen=True, slots=True)
class HostRoute:
    target_instance_id: str
    route_id: str
    policy: SharedEgressPolicy | None = None

    def __post_init__(self) -> None:
        if not self.target_instance_id or not self.route_id:
            raise ValueError("authorized target and route are required")


class HostTargetDenied(RuntimeError):
    """The host resolver did not authorize this physical request."""


class HostResponseTooLarge(RuntimeError):
    """An explicitly configured response byte bound was exceeded."""


class _Relay(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.head: asyncio.Future[httpx.Response] = asyncio.get_running_loop().create_future()
        self.queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue(maxsize=1)
        self.task: asyncio.Task[None] | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def aclose(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        outcomes = await asyncio.gather(self.task, return_exceptions=True)
        if isinstance(outcomes[0], Exception):
            raise MeteringUnavailable("controlled HTTP pump failed") from outcomes[0]


class SharedHostTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        meter: SharedRedisMeter,
        *,
        public_rules_enabled: bool,
        resolve: Callable[[httpx.Request], HostRoute | None | Awaitable[HostRoute | None]],
        lifetime: RequestLifetime,
        invocation: TrustedInvocation | None = None,
        verify: ssl.SSLContext | bool = True,
        max_response_bytes: int | None = None,
        on_limit: Callable[[SharedLimitExceeded], None] | None = None,
        client: httpx.AsyncClient | None = None,
        observe: AttemptObserver | None = None,
    ) -> None:
        if max_response_bytes is not None and (
            isinstance(max_response_bytes, bool) or max_response_bytes <= 0
        ):
            raise ValueError("positive response byte bound required")
        self._sender = SharedHTTPTransport(meter, lifetime=lifetime, observe=observe)
        self._meter, self._resolve, self._invocation = meter, resolve, invocation
        if not isinstance(public_rules_enabled, bool):
            raise ValueError("explicit public policy control mode required")
        self._controlled = public_rules_enabled or invocation is not None
        self._timeout_seconds = lifetime.timeout_seconds
        self._on_limit = on_limit
        self._max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(verify=verify, retries=0),
            trust_env=False,
        )
        self._relays: set[_Relay] = set()
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._closed:
            raise RuntimeError("shared HTTP transport is closed")
        if request.url.scheme not in {"http", "https"} or request.url.userinfo or request.url.fragment:
            raise HostTargetDenied("invalid controlled HTTP target")
        resolved = self._resolve(request)
        route = await resolved if inspect.isawaitable(resolved) else resolved
        if route is None:
            raise HostTargetDenied("controlled HTTP target denied")
        relay = _Relay()
        self._relays.add(relay)
        relay.task = asyncio.create_task(self._pump(relay, request, route), name="shared-host-http")
        try:
            return await relay.head
        except BaseException:
            await relay.aclose()
            raise

    async def _pump(self, relay: _Relay, request: httpx.Request, route: HostRoute) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._forward(relay, request, route)
        except asyncio.CancelledError:
            if not relay.head.done():
                relay.head.cancel()
            raise
        except Exception as exc:
            if isinstance(exc, SharedLimitExceeded) and self._on_limit is not None:
                self._on_limit(exc)
            if isinstance(exc, TimeoutError) and not relay.head.done():
                exc = MeteringUnavailable("shared host request deadline exceeded")
            if not relay.head.done():
                relay.head.set_exception(exc)
            else:
                await relay.queue.put(exc)
        finally:
            self._relays.discard(relay)

    async def _forward(self, relay: _Relay, request: httpx.Request, route: HostRoute) -> None:
        policy = route.policy or (
            await self._meter.current_policy() if self._controlled else SharedEgressPolicy()
        )
        async with self._sender.stream(
            self._client,
            request,
            policy=policy,
            target_instance_id=route.target_instance_id,
            route_id=route.route_id,
            invocation=self._invocation,
        ) as response:
            extensions = {**response.extensions, "egress_source": "UPSTREAM"}
            relay.head.set_result(
                httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    extensions=extensions,
                    stream=relay,
                    request=request,
                )
            )
            await self._relay_body(relay, response)
        await relay.queue.put(None)

    async def _relay_body(self, relay: _Relay, response: httpx.Response) -> None:
        received = 0
        async for chunk in response.aiter_raw():
            received += len(chunk)
            if self._max_response_bytes is not None and received > self._max_response_bytes:
                raise HostResponseTooLarge("controlled response byte bound exceeded")
            await relay.queue.put(chunk)

    async def aclose(self) -> None:
        self._closed = True
        try:
            outcomes = await asyncio.gather(
                *(relay.aclose() for relay in tuple(self._relays)),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
        finally:
            if self._owns_client:
                await self._client.aclose()
