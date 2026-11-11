"""Track ownership of standard urllib3 connections for synchronous cancellation.

This is the explicit, version-tested urllib3 boundary. DNS/blocked interpreter
termination still requires the parent execution supervisor; socket I/O is cut
without waiting for a Python buffered reader lock.
"""
from __future__ import annotations

import logging
import socket
import threading
import weakref
from collections.abc import Callable
from contextvars import ContextVar
from typing import Protocol, cast

from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool

from .egress_ipc_client import IPCLease

logger = logging.getLogger(__name__)
_CURRENT: ContextVar[PhysicalAttempt | None] = ContextVar("egress_physical_attempt", default=None)
_OWNERS: weakref.WeakKeyDictionary[HTTPConnection, PhysicalAttempt] = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()


class PhysicalAttempt:
    def __init__(self, lease: IPCLease) -> None:
        self.lease = lease
        self._connections: set[HTTPConnection] = set()
        self._sockets: dict[HTTPConnection, socket.socket] = {}
        self._complete = False
        lease.on_cancel(self.cancel)

    def bind(self, connection: HTTPConnection) -> None:
        with _LOCK:
            if self._complete:
                return
            _OWNERS[connection] = self
            self._connections.add(connection)
            sock = connection.sock
            if sock is not None:
                if not isinstance(sock, socket.socket):
                    raise RuntimeError("UNSUPPORTED_REQUESTS_SOCKET")
                self._sockets[connection] = sock
        try:
            self.lease.raise_if_failed()
        except BaseException:
            self.cancel()
            raise

    def cancel(self) -> None:
        with _LOCK:
            if self._complete:
                return
            for connection in self._connections:
                if _OWNERS.get(connection) is not self:
                    continue
                sock = connection.sock or self._sockets.get(connection)
                if sock is not None and sock.fileno() >= 0:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        logger.warning("requests_egress_socket_shutdown_unconfirmed")
                    sock.close()

    def complete(self) -> None:
        with _LOCK:
            self._complete = True
            for connection in self._connections:
                if _OWNERS.get(connection) is self:
                    del _OWNERS[connection]
            self._connections.clear()
            self._sockets.clear()
        self.lease.on_cancel(None)


class _PoolRequest(Protocol):
    def __call__(self, pool: HTTPConnectionPool, connection: HTTPConnection,
                 *args: object, **kwargs: object) -> object: ...


class _Connect(Protocol):
    def __call__(self, connection: HTTPConnection) -> None: ...


def _replace(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


class TransportWatch:
    def __init__(self) -> None:
        self._request = cast(_PoolRequest, HTTPConnectionPool._make_request)
        self._http_connect = cast(_Connect, HTTPConnection.connect)
        self._https_connect = cast(_Connect, HTTPSConnection.connect)

    def install(self) -> None:
        def request(pool: HTTPConnectionPool, connection: HTTPConnection,
                    *args: object, **kwargs: object) -> object:
            active = _CURRENT.get()
            if active is not None:
                if type(connection) not in (HTTPConnection, HTTPSConnection):
                    raise RuntimeError("UNSUPPORTED_REQUESTS_CONNECTION")
                active.bind(connection)
            return self._request(pool, connection, *args, **kwargs)

        def http_connect(connection: HTTPConnection) -> None:
            self._http_connect(connection)
            if (active := _CURRENT.get()) is not None:
                active.bind(connection)

        def https_connect(connection: HTTPConnection) -> None:
            self._https_connect(connection)
            if (active := _CURRENT.get()) is not None:
                active.bind(connection)

        _replace(HTTPConnectionPool, "_make_request", request)
        _replace(HTTPConnection, "connect", http_connect)
        _replace(HTTPSConnection, "connect", https_connect)

    def restore(self) -> None:
        _replace(HTTPConnectionPool, "_make_request", self._request)
        _replace(HTTPConnection, "connect", self._http_connect)
        _replace(HTTPSConnection, "connect", self._https_connect)



def run_attempt[Result](attempt: PhysicalAttempt, callback: Callable[[], Result]) -> Result:
    token = _CURRENT.set(attempt)
    try:
        attempt.lease.raise_if_failed()
        return callback()
    finally:
        _CURRENT.reset(token)
