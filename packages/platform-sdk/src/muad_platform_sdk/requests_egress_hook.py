"""Opt-in process-local requests send hook for one invocation's IPC client.

Installation in system site and supervisor deadlines are separate integration
steps. Only the standard HTTPAdapter with hidden retries disabled is supported.
Native redirects, authentication hooks, cookies and response semantics remain
owned by requests; each resulting adapter send acquires its own permit.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextvars import ContextVar
from types import TracebackType
from typing import Literal, Protocol, Self, cast
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.models import PreparedRequest, Response
from requests.sessions import Session
from urllib3.response import HTTPResponse

from .egress_ipc_client import IPCAdmissionClient, IPCAdmissionError
from .egress_ipc_protocol import AttemptMetadata
from .requests_deadline import deadline_arguments
from .requests_transport_watch import PhysicalAttempt, TransportWatch, run_attempt

logger = logging.getLogger(__name__)


class _AdapterSend(Protocol):
    def __call__(self, adapter: HTTPAdapter, request: PreparedRequest,
                 *args: object, **kwargs: object) -> Response: ...


class _SessionSend(Protocol):
    def __call__(self, session: Session, request: PreparedRequest, **kwargs: object) -> Response: ...


class _Redirects(Protocol):
    def __call__(self, session: Session, response: Response, request: PreparedRequest,
                 *args: object, **kwargs: object) -> Generator[PreparedRequest | Response, None, None]: ...


_REDIRECT: ContextVar[bool] = ContextVar("egress_controlled_redirect", default=False)
_CURRENT_SEND: ContextVar[tuple[PreparedRequest, bool] | None] = ContextVar(
    "egress_current_send", default=None,
)


class UnsupportedRequestsConfiguration(RuntimeError):
    pass


def _check_adapter(adapter: requests.adapters.BaseAdapter) -> None:
    if type(adapter) is not HTTPAdapter:
        raise UnsupportedRequestsConfiguration("UNSUPPORTED_REQUESTS_ADAPTER")
    if adapter.max_retries.total not in (0, False):
        raise UnsupportedRequestsConfiguration("UNSUPPORTED_REQUESTS_RETRY")


def _metadata(request: PreparedRequest) -> AttemptMetadata:
    parsed = urlsplit(request.url or "")
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedRequestsConfiguration("UNSUPPORTED_REQUESTS_URL_CREDENTIALS")
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parsed.scheme == "https" else 80
    port = f":{parsed.port}" if parsed.port is not None and parsed.port != default else ""
    authority = request.headers.get("Host")
    if isinstance(authority, bytes):
        authority = authority.decode("latin-1")
    return AttemptMetadata(method=request.method or "", origin=f"{parsed.scheme}://{host}{port}",
                           path=parsed.path or "/", authority=authority)


def _replace_method(target: object, name: str, replacement: object) -> None:
    """The one explicit dynamic boundary for process-local transport interception."""
    setattr(target, name, replacement)


class _ResponseLease:
    def __init__(self, raw: HTTPResponse, attempt: PhysicalAttempt, status_code: int) -> None:
        self._raw, self._lease, self._attempt = raw, attempt.lease, attempt
        self._read, self._stream, self._close = raw.read, raw.stream, raw.close
        self._status_code = status_code
        self._error_code: Literal["TRANSPORT_ERROR", "RESPONSE_INCOMPLETE"] | None = None
        self._finished = False
        self._lock = threading.Lock()
        # Keep the actual urllib3 response instance and its remaining methods.
        _replace_method(raw, "read", self.read)
        _replace_method(raw, "stream", self.stream)
        _replace_method(raw, "close", self.close)

    def _finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._attempt.complete()
        try:
            self._lease.finish(status_code=self._status_code, error_code=self._error_code)
        except IPCAdmissionError:
            logger.warning("requests_egress_completion_unconfirmed")

    def read(self, amt: int | None = None, decode_content: bool | None = None,
             cache_content: bool = False) -> bytes:
        try:
            data = self._read(amt, decode_content=decode_content, cache_content=cache_content)
            if not self._finished:
                self._lease.raise_if_failed()
        except BaseException as exc:
            failure = self._lease.failure
            self._abort()
            if failure is not None:
                raise failure from exc
            raise
        if (amt != 0 and not data) or self._raw.isclosed():
            self._finish()
        return data

    def stream(
        self, amt: int | None = 65536, decode_content: bool | None = None,
    ) -> Generator[bytes, None, None]:
        try:
            yield from self._stream(amt, decode_content=decode_content)
        except BaseException as exc:
            failure = self._lease.failure
            self._abort()
            if failure is not None:
                raise failure from exc
            raise
        else:
            self.close()

    def close(self) -> None:
        if not self._raw.isclosed() and self._error_code is None:
            self._error_code = "RESPONSE_INCOMPLETE"
        try:
            self._close()
        except BaseException:
            self._lease.close()
            raise
        self._finish()

    def _abort(self) -> None:
        self._error_code = "TRANSPORT_ERROR"
        try:
            self.close()
        except OSError:
            logger.warning("requests_egress_transport_close_unconfirmed")


_INSTALL_LOCK = threading.RLock()
_ACTIVE: RequestsEgressHook | None = None


class RequestsEgressHook:
    def __init__(self, client: IPCAdmissionClient) -> None:
        self._client = client
        self._adapter_send = cast(_AdapterSend, HTTPAdapter.send)
        self._session_send = cast(_SessionSend, Session.send)
        self._redirects = cast(_Redirects, Session.resolve_redirects)
        self._transport = TransportWatch()

    def __enter__(self) -> Self:
        global _ACTIVE
        with _INSTALL_LOCK:
            if _ACTIVE is not None:
                raise UnsupportedRequestsConfiguration("REQUESTS_HOOK_ALREADY_INSTALLED")
            self._client.probe()

            def send(adapter: HTTPAdapter, request: PreparedRequest,
                     *args: object, **kwargs: object) -> Response:
                return self._send(adapter, request, *args, **kwargs)

            def session_send(session: Session, request: PreparedRequest, **kwargs: object) -> Response:
                return self._session(session, request, **kwargs)

            def redirects(
                session: Session, response: Response, request: PreparedRequest,
                *args: object, **kwargs: object,
            ) -> Generator[PreparedRequest | Response, None, None]:
                yield from self._resolve(session, response, request, *args, **kwargs)

            self._transport.install()
            _replace_method(Session, "resolve_redirects", redirects)
            _replace_method(HTTPAdapter, "send", send)
            _replace_method(Session, "send", session_send)
            _ACTIVE = self
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, traceback: TracebackType | None) -> None:
        global _ACTIVE
        with _INSTALL_LOCK:
            _replace_method(HTTPAdapter, "send", self._adapter_send)
            _replace_method(Session, "send", self._session_send)
            _replace_method(Session, "resolve_redirects", self._redirects)
            self._transport.restore()
            _ACTIVE = None

    def _session(self, session: Session, request: PreparedRequest, **kwargs: object) -> Response:
        _check_adapter(session.get_adapter(request.url or ""))
        current = _CURRENT_SEND.set((request, _REDIRECT.get()))
        redirect = _REDIRECT.set(False)
        try:
            return self._session_send(session, request, **kwargs)
        finally:
            _REDIRECT.reset(redirect)
            _CURRENT_SEND.reset(current)

    def _resolve(self, session: Session, response: Response, request: PreparedRequest,
                 *args: object, **kwargs: object) -> Generator[PreparedRequest | Response, None, None]:
        iterator = self._redirects(session, response, request, *args, **kwargs)
        controlled = bool(getattr(response, "_muad_egress_controlled", False))
        while True:
            token = _REDIRECT.set(controlled)
            try:
                result = next(iterator)
            except StopIteration:
                return
            finally:
                _REDIRECT.reset(token)
            if isinstance(result, Response):
                controlled = controlled or bool(getattr(result, "_muad_egress_controlled", False))
            yield result

    def _send(self, adapter: HTTPAdapter, request: PreparedRequest,
              *args: object, **kwargs: object) -> Response:
        _check_adapter(adapter)
        lease = self._client.begin(_metadata(request))
        if lease is None:
            current = _CURRENT_SEND.get()
            if current is not None and current[0] is request and current[1]:
                raise IPCAdmissionError("IPC_TARGET_DENIED")
            return self._adapter_send(adapter, request, *args, **kwargs)
        attempt = PhysicalAttempt(lease)
        try:
            bounded_args, bounded_kwargs = deadline_arguments(args, kwargs, lease.remaining_seconds)
            response = run_attempt(
                attempt, lambda: self._adapter_send(adapter, request, *bounded_args, **bounded_kwargs),
            )
        except BaseException as exc:
            failure = lease.failure
            attempt.complete()
            if failure is None:
                try:
                    lease.finish(error_code="TRANSPORT_ERROR")
                except IPCAdmissionError:
                    logger.warning("requests_egress_failure_report_unconfirmed")
            lease.close()
            if failure is not None:
                raise failure from exc
            raise
        if not isinstance(response.raw, HTTPResponse):
            response.close()
            attempt.complete()
            lease.close()
            raise UnsupportedRequestsConfiguration("UNSUPPORTED_REQUESTS_RESPONSE")
        _replace_method(response, "_muad_egress_controlled", True)
        _ResponseLease(response.raw, attempt, response.status_code)
        return response
