"""Invocation-private host broker. It grants permits; it never forwards HTTP.

The supervisor must enforce the bound deadline and fencing on the script. IPC
EOF alone does not prove HTTP closure, so unconfirmed leases are kept to expiry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from pydantic import ValidationError

from .egress_ipc_client import IPCAdmissionClient as IPCAdmissionClient
from .egress_ipc_client import IPCAdmissionError as IPCAdmissionError
from .egress_ipc_protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    BeginRequest,
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


@dataclass(frozen=True, slots=True)
class UncontrolledTarget:
    """Host authorization for independent non-business traffic; never a fallback."""


class InvocationEgressBroker:
    def __init__(
        self, *, meter: SharedRedisMeter, policy: SharedEgressPolicy, invocation: TrustedInvocation,
        resolve: Callable[[AttemptMetadata], EgressTarget | UncontrolledTarget | None],
        deadline_seconds: float,
        cleanup_seconds: float, max_connections: int = 32, renewal_interval_seconds: float = 1.0,
    ) -> None:
        lifetimes = (deadline_seconds, cleanup_seconds, renewal_interval_seconds)
        if (any(not math.isfinite(v) or v <= 0 for v in lifetimes)
                or not math.isfinite(deadline_seconds + cleanup_seconds) or max_connections <= 0):
            raise ValueError("valid IPC lifetime and resource bounds required")
        self._meter, self._policy, self._invocation = meter, policy, invocation
        self._resolve, self._deadline, self._cleanup = resolve, deadline_seconds, cleanup_seconds
        self._max_connections = max_connections
        self._renewal_interval = renewal_interval_seconds
        self._handlers: set[asyncio.Task[None]] = set()
        self.exhausted = asyncio.Event()
        self.exhausted_scope: LimitScope | None = None
        self.socket_path = Path()
        self._server: asyncio.Server | None = None
        self._expires_at = 0.0
        self._closing = False

    async def __aenter__(self) -> Self:
        self._expires_at = asyncio.get_running_loop().time() + self._deadline
        directory = Path(tempfile.mkdtemp(prefix="fx-egress-", dir="/tmp"))
        os.chmod(directory, 0o700)
        self.socket_path = directory / "broker.sock"
        try:
            self._server = await asyncio.start_unix_server(
                self._handle, path=str(self.socket_path), limit=MAX_FRAME_BYTES,
            )
            os.chmod(self.socket_path, 0o600)
        except BaseException:
            self.socket_path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc: BaseException | None, traceback: TracebackType | None) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        tasks = list(self._handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.rmdir()

    async def _reply(self, writer: asyncio.StreamWriter, value: dict[str, object]) -> None:
        writer.write(frame(value))
        await writer.drain()

    async def _begin(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     request: BeginRequest) -> None:
        target = self._resolve(request)
        if target is None:
            await self._reply(writer, {"ok": False, "code": "IPC_TARGET_DENIED"})
            return
        if self.exhausted.is_set():
            await self._error_reply(writer, "IPC_LIMIT_TOTAL", scope=self.exhausted_scope)
            return
        if isinstance(target, UncontrolledTarget):
            await self._reply(writer, {"ok": True, "controlled": False})
            return
        attempt_id = uuid.uuid4().hex
        permit = await self._meter.admit(
            self._policy, target_instance_id=target.target_instance_id, route_id=target.route_id,
            attempt_id=attempt_id, invocation=self._invocation,
            lease_seconds=self._expires_at - asyncio.get_running_loop().time() + self._cleanup,
        )
        if permit is not None:
            await self._meter.consume(permit)
        await self._reply(writer, {"ok": True, "controlled": True, "attempt_id": attempt_id,
                                   "remaining_seconds": self._expires_at - asyncio.get_running_loop().time()})
        completion = await self._completion(reader, permit)
        if completion.op != "done":
            raise ValueError("completion required")
        if permit is not None:
            await self._meter.release(permit)
        await self._reply(writer, {"ok": True})

    async def _completion(self, reader: asyncio.StreamReader, permit: SharedPermit | None) -> ControlRequest:
        incoming = asyncio.create_task(reader.readline())
        try:
            while not incoming.done():
                await asyncio.wait({incoming}, timeout=self._renewal_interval)
                if not incoming.done() and permit is not None and permit.rule_ids:
                    remaining = self._expires_at - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError("execution deadline")
                    await self._meter.renew(permit, lease_seconds=remaining + self._cleanup)
            return ControlRequest.model_validate_json(incoming.result())
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
            if exc.kind == "TOTAL" and exc.scope in {"execution", "invocation"}:
                self.exhausted_scope = exc.scope
                self.exhausted.set()
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

    async def _error_reply(self, writer: asyncio.StreamWriter, code: str, *,
                           scope: LimitScope | None = None) -> None:
        payload: dict[str, object] = {"ok": False, "code": code}
        if scope is not None:
            payload["scope"] = scope
        try:
            await self._reply(writer, payload)
        except OSError:
            logger.warning("egress_ipc_error_reply_unavailable")
