from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from fluxion.runtime.context import RuntimeContext


class A2AError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class A2AAuth:
    bearer_token: str

    def headers(self) -> dict[str, str]:
        return {"type": "bearer", "token": self.bearer_token}


@dataclass(frozen=True, slots=True)
class A2ARequest:
    tenant_id: str
    user_id: str
    execution_id: str
    trace_id: str
    target_agent_id: str
    body: dict[str, object]
    auth: dict[str, str]


@dataclass(frozen=True, slots=True)
class A2AResponse:
    ok: bool
    body: dict[str, object]
    trace_id: str


class A2APeer(Protocol):
    async def handle(self, request: A2ARequest) -> A2AResponse: ...


class A2AAdapter:
    def __init__(self, *, peer: A2APeer, auth: A2AAuth, timeout_ms: int = 30_000) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._peer = peer
        self._auth = auth
        self._timeout_ms = timeout_ms

    async def request(
        self,
        context: RuntimeContext,
        *,
        target_agent_id: str,
        body: dict[str, object],
    ) -> A2AResponse:
        request = A2ARequest(
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            execution_id=context.snapshot.execution_id,
            trace_id=context.snapshot.trace_id,
            target_agent_id=target_agent_id,
            body=body,
            auth=self._auth.headers(),
        )
        try:
            response = await asyncio.wait_for(
                self._peer.handle(request),
                timeout=self._timeout_ms / 1000,
            )
        except TimeoutError as exc:
            raise A2AError("a2a_timeout", "A2A peer timed out") from exc
        context.emit("a2a.completed", {"target_agent_id": target_agent_id, "ok": response.ok})
        return response


class StubA2APeer:
    def __init__(self, *, expected_token: str, response_body: dict[str, object]) -> None:
        self._expected_token = expected_token
        self._response_body = response_body
        self.requests: list[A2ARequest] = []

    async def handle(self, request: A2ARequest) -> A2AResponse:
        self.requests.append(request)
        if request.auth.get("token") != self._expected_token:
            raise A2AError("a2a_auth_failed", "invalid A2A bearer token")
        return A2AResponse(ok=True, body=self._response_body, trace_id=request.trace_id)
