"""Invocation-private host broker. It grants permits; it never forwards HTTP.

The supervisor must enforce the bound deadline and fencing on the script. IPC
EOF alone does not prove HTTP closure, so unconfirmed leases are kept to expiry.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from pydantic import ValidationError

from .egress_attempt_audit import AttemptAudit, AttemptObserver
from .egress_ipc_client import IPCAdmissionClient as IPCAdmissionClient
from .egress_ipc_client import IPCAdmissionError as IPCAdmissionError
from .egress_ipc_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    BeginRequest,
    CompletionRequest,
    ControlRequest,
    frame,
)
from .egress_ipc_protocol import AttemptMetadata as AttemptMetadata
from .shared_redis import LimitScope, MeteringUnavailable, SharedLimitExceeded, SharedPermit, SharedRedisMeter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EgressTarget:
    target_instance_id: str
    route_id: str
    policy: SharedEgressPolicy | None = None


@dataclass(frozen=True, slots=True)
class UncontrolledTarget:
    """Host authorization for independent non-business traffic; never a fallback."""


class InvocationEgressBroker:
    def __init__(
        self,
        *,
        meter: SharedRedisMeter,
        invocation: TrustedInvocation,
        resolve: Callable[
            [AttemptMetadata],
            EgressTarget | UncontrolledTarget | None | Awaitable[EgressTarget | UncontrolledTarget | None],
        ],
        deadline_seconds: float,
        cleanup_seconds: float,
        max_connections: int = 32,
        renewal_interval_seconds: float = 1.0,
        observe: AttemptObserver | None = None,
    ) -> None:
        lifetimes = (deadline_seconds, cleanup_seconds, renewal_interval_seconds)
        if (
            any(not math.isfinite(v) or v <= 0 for v in lifetimes)
            or not math.isfinite(deadline_seconds + cleanup_seconds)
            or max_connections <= 0
        ):
            raise ValueError("valid IPC lifetime and resource bounds required")
        self._meter, self._invocation = meter, invocation
        self._resolve, self._deadline, self._cleanup = resolve, deadline_seconds, cleanup_seconds
        self._observe = observe
        self._max_connections = max_connections
        self._renewal_interval = renewal_interval_seconds
        self._handlers: set[asyncio.Task[None]] = set()
        self.exhausted = asyncio.Event()
        self.clients_closed = asyncio.Event()
        self.exhausted_scope: LimitScope | None = None
        self._pending: dict[str, SharedPermit] = {}
        self.socket_path = Path()
        self._server: asyncio.Server | None = None
        self._expires_at = 0.0
        self._closing = False
        self._child_started = False
        self._ever_admitted = False

    async def __aenter__(self) -> Self:
        if self._server is not None or self._closing:
            raise RuntimeError("invocation broker is single-use")
        self._expires_at = asyncio.get_running_loop().time() + self._deadline
        directory = Path(tempfile.mkdtemp(prefix="fx-egress-", dir="/tmp"))
        os.chmod(directory, 0o700)
        self.socket_path = directory / "broker.sock"
        try:
            self._server = await asyncio.start_unix_server(
                self._handle,
                path=str(self.socket_path),
                limit=MAX_FRAME_BYTES,
            )
            os.chmod(self.socket_path, 0o600)
            if self._closing:
                raise MeteringUnavailable("invocation closed during startup")
        except BaseException:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
            self.socket_path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.stop_admissions()
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.rmdir()

    async def stop_admissions(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        tasks = list(self._handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()

    async def confirm_clients_closed(self) -> None:
        """Only the supervising host may call this after child/HTTP termination.

        IPC EOF alone is insufficient; ordinary __aexit__ never releases these
        permits. Counts remain charged even when confirmed occupancy is released.
        """
        await self.stop_admissions()
        for permit in tuple(self._pending.values()):
            try:
                await self._meter.release(permit)
            except MeteringUnavailable:
                logger.warning("egress_ipc_stopped_lease_release_failed")
            else:
                self._pending.pop(permit.attempt_id, None)
        self.clients_closed.set()

    def note_limit(self, error: SharedLimitExceeded) -> None:
        if error.kind == "TOTAL" and error.scope in {"execution", "invocation"}:
            self.exhausted_scope = error.scope
            self.exhausted.set()

    def start_child(self) -> None:
        """Host-only barrier immediately before the sandbox runner may spawn."""
        if self._closing or self._child_started:
            raise MeteringUnavailable("invocation cannot start another child")
        self._child_started = True

    async def confirm_idle(self) -> None:
        """After stop_admissions: no native child and no admitted sender existed."""
        if not self._closing:
            raise RuntimeError("close admissions before confirming idle invocation")
        if not self._child_started and not self._ever_admitted:
            await self.confirm_clients_closed()

    async def _reply(self, writer: asyncio.StreamWriter, value: dict[str, object]) -> None:
        writer.write(frame(value))
        await writer.drain()

    async def _begin(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, request: BeginRequest
    ) -> None:
        resolved = self._resolve(request)
        target = await resolved if inspect.isawaitable(resolved) else resolved
        if target is None:
            await self._reply(writer, {"ok": False, "code": "IPC_TARGET_DENIED"})
            return
        if isinstance(target, UncontrolledTarget):
            await self._reply(writer, {"ok": True, "controlled": False})
            return
        audit = AttemptAudit(self._observe, origin=request.origin, path=request.path,
                             method=request.method)
        try:
            await self._controlled_begin(reader, writer, target, audit)
        finally:
            await audit.finish(sys.exception())

    async def _controlled_begin(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        target: EgressTarget, audit: AttemptAudit,
    ) -> None:
        if self.exhausted.is_set():
            raise SharedLimitExceeded("TOTAL", "", 0, scope=self.exhausted_scope or "execution")
        policy = target.policy or await self._meter.current_policy()
        permit = await self._meter.admit(
            policy,
            target_instance_id=target.target_instance_id,
            route_id=target.route_id,
            attempt_id=audit.attempt_id,
            invocation=self._invocation,
            lease_seconds=self._expires_at - asyncio.get_running_loop().time() + self._cleanup,
        )
        if permit is not None:
            if permit.rule_ids:
                self._pending[permit.attempt_id] = permit
            await self._meter.consume(permit)
            self._ever_admitted = True
        await self._reply(writer, {
            "ok": True, "controlled": True, "attempt_id": audit.attempt_id,
            "remaining_seconds": self._expires_at - asyncio.get_running_loop().time(),
        })
        audit.sent = True
        completion = await self._completion(reader, permit)
        audit.status_code, audit.error_code = completion.status_code, completion.error_code
        if permit is not None:
            await self._meter.release(permit)
            self._pending.pop(permit.attempt_id, None)
        # The observer runs before the acknowledgement, so callers can inspect
        # a completed attempt immediately after consuming/closing its response.
        await audit.finish()
        await self._reply(writer, {"ok": True})

    async def _completion(
        self, reader: asyncio.StreamReader, permit: SharedPermit | None,
    ) -> CompletionRequest:
        incoming = asyncio.create_task(reader.readline())
        try:
            while not incoming.done():
                await asyncio.wait({incoming}, timeout=self._renewal_interval)
                if not incoming.done() and permit is not None:
                    remaining = self._expires_at - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError("execution deadline")
                    await self._meter.renew(permit, lease_seconds=remaining + self._cleanup)
            return CompletionRequest.model_validate_json(incoming.result())
        finally:
            incoming.cancel()
            await asyncio.gather(incoming, return_exceptions=True)

    async def _dispatch(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        if not line.endswith(b"\n") or len(line) > MAX_FRAME_BYTES:
            raise ValueError("invalid IPC frame")
        value = json.loads(line)
        if isinstance(value, dict) and value.get("op") == "probe":
            ControlRequest.model_validate(value)
            await self._reply(writer, {"ok": True, "protocol": PROTOCOL_VERSION})
        else:
            await self._begin(reader, writer, BeginRequest.model_validate(value))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("IPC handler requires a task")
        try:
            if asyncio.get_running_loop().time() >= self._expires_at:
                await self._reply(writer, {"ok": False, "code": "IPC_EXECUTION_EXPIRED"})
                return
            if self._closing or len(self._handlers) >= self._max_connections:
                await self._reply(writer, {"ok": False, "code": "IPC_BUSY"})
                return
            self._handlers.add(task)
            async with asyncio.timeout_at(self._expires_at):
                await self._dispatch(reader, writer)
        except SharedLimitExceeded as exc:
            self.note_limit(exc)
            await self._error_reply(writer, "IPC_LIMIT_" + exc.kind, scope=exc.scope)
        except MeteringUnavailable:
            await self._error_reply(writer, "IPC_DEPENDENCY_UNAVAILABLE")
        except (ValueError, ValidationError, RecursionError):
            await self._error_reply(writer, "IPC_INVALID_REQUEST")
        except TimeoutError:
            await self._error_reply(writer, "IPC_EXECUTION_EXPIRED")
        except OSError:
            logger.warning("egress_ipc_connection_incomplete")
        finally:
            self._handlers.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                logger.warning("egress_ipc_connection_close_unconfirmed")

    async def _error_reply(
        self, writer: asyncio.StreamWriter, code: str, *, scope: LimitScope | None = None
    ) -> None:
        payload: dict[str, object] = {"ok": False, "code": code}
        if scope is not None:
            payload["scope"] = scope
        try:
            await self._reply(writer, payload)
        except OSError:
            logger.warning("egress_ipc_error_reply_unavailable")
