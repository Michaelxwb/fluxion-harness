"""Synchronous, credential-free client used by the requests transport hook."""
from __future__ import annotations

import math
import select
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from .egress_ipc_protocol import MAX_FRAME_BYTES, PROTOCOL_VERSION, AttemptMetadata, WireResponse, frame


class IPCAdmissionError(RuntimeError):
    def __init__(
        self, code: str, *, scope: Literal["public", "execution", "invocation"] | None = None,
    ) -> None:
        self.code = code
        self.scope = scope
        super().__init__(code)


def _receive(connection: socket.socket) -> WireResponse:
    try:
        data = bytearray()
        while not data.endswith(b"\n") and len(data) <= MAX_FRAME_BYTES:
            chunk = connection.recv(1)
            if not chunk:
                break
            data.extend(chunk)
        result = WireResponse.decode(bytes(data))
    except (OSError, ValueError) as exc:
        raise IPCAdmissionError("IPC_UNAVAILABLE") from exc
    if not result.ok:
        # Only fixed protocol codes are surfaced, never arbitrary server text.
        codes = {"IPC_INVALID_REQUEST", "IPC_TARGET_DENIED", "IPC_DEPENDENCY_UNAVAILABLE",
                 "IPC_LIMIT_TOTAL", "IPC_LIMIT_WINDOW", "IPC_LIMIT_INFLIGHT", "IPC_BUSY",
                 "IPC_EXECUTION_EXPIRED"}
        code = result.code if result.code in codes else "IPC_UNAVAILABLE"
        raise IPCAdmissionError(code, scope=result.scope)
    return result


def _exchange(connection: socket.socket, payload: dict[str, object]) -> WireResponse:
    try:
        connection.sendall(frame(payload))
    except OSError as exc:
        raise IPCAdmissionError("IPC_UNAVAILABLE") from exc
    return _receive(connection)


class IPCLease:
    """One receiver owns IPC reads; failure wakes and closes its physical attempt."""

    def __init__(self, connection: socket.socket, *, deadline: float, timeout: float) -> None:
        self._connection, self._deadline, self._timeout = connection, deadline, timeout
        self._lock = threading.Lock()
        self._stop, self._ack = threading.Event(), threading.Event()
        self._closed, self._completing = False, False
        self._failure: IPCAdmissionError | None = None
        self._callback: Callable[[], None] | None = None
        self._thread = threading.Thread(target=self._watch, name="egress-ipc-watch", daemon=True)
        self._thread.start()

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    @property
    def failure(self) -> IPCAdmissionError | None:
        with self._lock:
            failure = self._failure
        if failure is None and not self._stop.is_set() and self.remaining_seconds <= 0:
            return IPCAdmissionError("IPC_EXECUTION_EXPIRED")
        return failure

    def raise_if_failed(self) -> None:
        failure = self.failure
        if failure is not None:
            raise failure

    def on_cancel(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._callback = callback
            failed = self._failure is not None
        if failed and callback is not None:
            callback()

    def _fail(self, failure: IPCAdmissionError) -> None:
        with self._lock:
            if self._stop.is_set() or self._failure is not None:
                return
            self._failure = failure
            callback = self._callback
        self._ack.set()
        if callback is not None:
            callback()

    def _watch(self) -> None:
        try:
            while not self._stop.is_set():
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise IPCAdmissionError("IPC_EXECUTION_EXPIRED")
                readable, _, _ = select.select([self._connection], [], [], min(remaining, 0.1))
                if readable:
                    self._connection.settimeout(min(remaining, self._timeout))
                    _receive(self._connection)
                    with self._lock:
                        completing = self._completing
                    if not completing:
                        raise IPCAdmissionError("IPC_UNAVAILABLE")
                    self._ack.set()
                    return
        except IPCAdmissionError as exc:
            self._fail(exc)
        except (OSError, ValueError):
            self._fail(IPCAdmissionError("IPC_UNAVAILABLE"))

    def finish(self, *, status_code: int | None = None,
               error_code: Literal["TRANSPORT_ERROR", "RESPONSE_INCOMPLETE"] | None = None) -> None:
        """Confirm closure; do not race a second recv against the monitor."""
        with self._lock:
            if self._closed:
                return
            self._completing = True
        try:
            self.raise_if_failed()
            self._connection.sendall(frame({
                "op": "done", "status_code": status_code, "error_code": error_code,
            }))
            if not self._ack.wait(timeout=self._timeout):
                raise IPCAdmissionError("IPC_UNAVAILABLE")
            self.raise_if_failed()
        except OSError as exc:
            raise IPCAdmissionError("IPC_UNAVAILABLE") from exc
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._callback = None
        self._stop.set()
        self._connection.close()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=self._timeout + 0.2)


class IPCAdmissionClient:
    def __init__(self, socket_path: Path, *, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("positive finite IPC timeout required")
        self._path, self._timeout = socket_path, timeout_seconds

    def _connect(self) -> socket.socket:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self._timeout)
        try:
            connection.connect(str(self._path))
        except OSError as exc:
            connection.close()
            raise IPCAdmissionError("IPC_UNAVAILABLE") from exc
        return connection

    def probe(self) -> bool:
        with self._connect() as connection:
            response = _exchange(connection, {"op": "probe"})
        if response.protocol != PROTOCOL_VERSION:
            raise IPCAdmissionError("IPC_UNSUPPORTED_PROTOCOL")
        return True

    def begin(self, attempt: AttemptMetadata) -> IPCLease | None:
        started = time.monotonic()
        connection = self._connect()
        try:
            response = _exchange(connection, {"op": "begin", **attempt.model_dump()})
            if response.controlled is False:
                connection.close()
                return None
            if response.controlled is not True or response.remaining_seconds is None:
                raise IPCAdmissionError("IPC_UNAVAILABLE")
        except BaseException:
            connection.close()
            raise
        return IPCLease(connection, deadline=started + response.remaining_seconds, timeout=self._timeout)
