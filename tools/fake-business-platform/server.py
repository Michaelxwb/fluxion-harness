#!/usr/bin/env python3
"""Standalone business-platform simulator for session/SDK smoke tests.

Two modes:

- ``smoke_platform`` (default): cookie-based login used by the legacy
  session-manager smoke tests. Behaviour is unchanged.
- ``mssw``: models the MSSW gateway contract closely enough to exercise the
  real ``MSSWSessionAdapter`` (SigV4-style AK/SK login, CSRF cookie/header,
  health endpoint) and the Runtime Guard ``requests`` compat layer
  (Cookie/CSRF/Host/static headers/Traceid).

Independently of the platform mode, ``POST /mcp`` exposes a Streamable HTTP
JSON-RPC subset (initialize / notifications/initialized / tools/list /
tools/call) so the Console MCP client and the Runtime can be driven against a
real HTTP endpoint with no third-party MCP SDK.

The service intentionally has no session-manager import; it is a plain HTTP
server so both Node tests and Python skill scripts can drive it.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sys
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

MAX_BODY_BYTES = 8192
GATEWAY_PREFIX = "/gateway/"
MSSW_LOGIN_PATH = "/gateway/mss-auth-acl-service/v1/certification/login_agent"
MSSW_GET_TOKEN_PATH = "/v1/certification/get_token"
MSSW_HEALTH_PATH = "/v1/rtt"
MSSW_PING_PATH = "/api/v1/ping"
MSSW_TARGETS_PATH = "/api/v1/targets"
MSSW_TRACE_HEADER = "Traceid"

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_DEFAULT_PATH = "/mcp"
MCP_SERVER_NAME = "fake-business-platform"
MCP_SERVER_VERSION = "1.0.0"
MCP_SESSION_HEADER = "Mcp-Session-Id"
MCP_RPC_PARSE_ERROR = -32700
MCP_RPC_METHOD_NOT_FOUND = -32601
MCP_RPC_INVALID_PARAMS = -32602
MCP_RPC_UNAUTHORIZED = -32001

# Header names whose values must never reach the logs. Values of headers passed
# through ``--mcp-header`` are redacted as well (see ``is_secret_header``).
SENSITIVE_HEADER_NAMES = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
})

MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "platform.ping",
        "description": "Read-only liveness probe.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "platform.targets.list",
        "description": "Read-only target inventory.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "platform.echo",
        "description": "Echo the message argument back.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "platform.targets.delete",
        "description": "Delete one target by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "platform.fail",
        "description": "Always fails; used to exercise isError propagation.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
)


# Recognized logins for the legacy smoke_platform mode. Add a
# "username": "password" pair here and restart the service to enable another
# account; every account can be logged in at once.
ACCOUNTS: dict[str, str] = {
    "demo": "demo-pass",
    "michael": "michael-pass",
}

# Business error codes, modeled on MSSW: /login always answers HTTP 200 and the
# outcome lives in the JSON body's "code" field. Session smoke tests use these
# to exercise the session-manager error classification path end to end.
CODE_SUCCESS = 0
CODE_PARAMS_ERR = 1001
CODE_AUTH_FAILED = 1002
CODE_ACCOUNT_LOCKED = 1003
CODE_RATE_LIMITED = 1004
CODE_SERVICE_ERR = 1005

# Username prefixes that trigger a non-auth account-state error even when the
# password is correct, so tests can hit every classification branch. The rest
# of the name is arbitrary (e.g. "locked-alice").
ERROR_PREFIX_CODES: dict[str, int] = {
    "locked-": CODE_ACCOUNT_LOCKED,
    "ratelimited-": CODE_RATE_LIMITED,
    "serviceerr-": CODE_SERVICE_ERR,
}


class FakeBusinessPlatform(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        args: argparse.Namespace,
    ):
        super().__init__(address, handler)
        self.accounts: dict[str, str] = dict(ACCOUNTS)
        self.platform = args.platform
        self.cookie_name = args.cookie_name
        self.session_ttl = args.session_ttl
        self.verbose = args.verbose
        self.ak = args.ak
        self.sk = args.sk
        self.csrf_enabled = not args.no_csrf
        self.request_counts: dict[str, int] = {}
        # Tokens issued by /v1/certification/get_token. The adapter fetches the
        # token and sends it as a header on login without a cookie jar, so login
        # validates the header against this set; business calls validate the
        # cookie/header pair on the session.
        self.csrf_tokens: set[str] = set()
        # token -> (expires_at, username)
        self.sessions: dict[str, tuple[float, str]] = {}
        # Streamable HTTP MCP state (independent of the platform mode).
        self.mcp_path: str = args.mcp_path
        self.mcp_auth: str = args.mcp_auth
        self.mcp_token: str = args.mcp_token
        self.mcp_headers: tuple[tuple[str, str], ...] = tuple(args.mcp_headers)
        self.mcp_secret_header_names: frozenset[str] = frozenset(
            name.lower() for name, _ in self.mcp_headers
        )
        self.mcp_sessions: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    server: FakeBusinessPlatform

    def _log_headers(self) -> None:
        # 打印每个请求的方法/路径/协议与全部请求头，便于调试适配器是否真的带上了
        # hostHeader（如 Host / X-Tag）。写 stderr，与 BaseHTTPRequestHandler 日志一致。
        # 认证/会话/自定义头一律脱敏，避免把 MCP 令牌写进日志。
        lines = [f"{self.command} {self.path} {self.request_version}"]
        secret_names = self.server.mcp_secret_header_names
        for key, value in self.headers.items():
            shown = "***" if is_secret_header(key, secret_names) else value
            lines.append(f"  {key}: {shown}")
        sys.stderr.write("\n".join(lines) + "\n")

    def do_POST(self) -> None:
        self._log_headers()
        path = urlparse(self.path).path
        if path == self.server.mcp_path:
            self._mcp_post()
            return
        if self.server.platform == "mssw":
            self._mssw_login(path)
            return
        if path != "/login":
            self._send_json(404, {"error": "not_found"})
            return
        payload = self._read_json_body()
        if payload is None or not payload.get("username") or not payload.get("password"):
            self._send_business_error(CODE_PARAMS_ERR, "username and password are required")
            return
        username = str(payload.get("username"))
        for prefix, code in ERROR_PREFIX_CODES.items():
            if username.startswith(prefix):
                self._send_business_error(code, f"account {prefix.rstrip('-')}")
                return
        if not self._valid_credentials(payload):
            self._send_business_error(CODE_AUTH_FAILED, "invalid credentials")
            return
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + self.server.session_ttl
        self.server.sessions[token] = (expires_at, username)
        self._send_json(
            200,
            {
                "code": CODE_SUCCESS,
                "authenticated": True,
                "user": username,
                "expiresAt": iso_time(expires_at),
            },
            [("Set-Cookie", self._session_cookie(token))],
        )

    def do_GET(self) -> None:
        self._log_headers()
        parsed = urlparse(self.path)
        path = parsed.path
        if path == self.server.mcp_path:
            # Streamable HTTP MCP is POST-only in this subset; GET (SSE stream)
            # is not implemented, so advertise the allowed method explicitly.
            self._send_json(405, {"error": "method_not_allowed"}, [("Allow", "POST")])
            return
        if self.server.platform == "mssw":
            self._mssw_get(path)
            return
        if path == "/":
            self._send_html(200, self._home_page())
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if path == "/health/session":
            self._send_auth_json({"ok": True})
            return
        if path == "/api/me":
            identity = self._session_identity()
            if identity is None:
                self._send_json(401, {"authenticated": False})
                return
            self._send_json(200, {"authenticated": True, "user": identity[1]})
            return
        self._send_json(404, {"error": "not_found"})

    # ------------------------------------------------------------------ #
    # Streamable HTTP MCP subset
    # ------------------------------------------------------------------ #

    def _mcp_post(self) -> None:
        body = self._read_body_text()
        if body is None:
            self._send_json(400, rpc_error(None, MCP_RPC_PARSE_ERROR, "unreadable body"))
            return
        if not self._mcp_authorized():
            self._send_json(401, rpc_error(None, MCP_RPC_UNAUTHORIZED, "unauthorized"))
            return
        payload = parse_json_object(body)
        if payload is None:
            self._send_json(400, rpc_error(None, MCP_RPC_PARSE_ERROR, "invalid json"))
            return
        method = str(payload.get("method") or "")
        request_id = payload.get("id")
        if method == "initialize":
            self._mcp_initialize(request_id)
            return
        if not self._mcp_session_valid():
            self._send_json(
                401, rpc_error(request_id, MCP_RPC_UNAUTHORIZED, "missing or invalid session")
            )
            return
        if method == "notifications/initialized":
            self._send_empty(202)
            return
        if method == "tools/list":
            self._send_json(200, rpc_result(request_id, {"tools": list(MCP_TOOLS)}))
            return
        if method == "tools/call":
            self._mcp_tools_call(request_id, payload.get("params"))
            return
        self._send_json(
            200, rpc_error(request_id, MCP_RPC_METHOD_NOT_FOUND, f"unknown method: {method}")
        )

    def _mcp_authorized(self) -> bool:
        mode = self.server.mcp_auth
        if mode == "none":
            return True
        if mode == "bearer":
            expected = f"Bearer {self.server.mcp_token}"
            return constant_time_equals(self.headers.get("Authorization", ""), expected)
        for name, value in self.server.mcp_headers:
            received = self.headers.get(name)
            if received is None or not constant_time_equals(received, value):
                return False
        return True

    def _mcp_session_valid(self) -> bool:
        session_id = self.headers.get(MCP_SESSION_HEADER, "")
        return bool(session_id) and session_id in self.server.mcp_sessions

    def _mcp_initialize(self, request_id: Any) -> None:
        session_id = secrets.token_urlsafe(24)
        self.server.mcp_sessions.add(session_id)
        result = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        }
        self._send_json(200, rpc_result(request_id, result), [(MCP_SESSION_HEADER, session_id)])

    def _mcp_tools_call(self, request_id: Any, params: Any) -> None:
        call = params if isinstance(params, dict) else {}
        name = str(call.get("name") or "")
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = call_mcp_tool(name, arguments)
        except UnknownMcpToolError:
            self._send_json(
                200, rpc_error(request_id, MCP_RPC_INVALID_PARAMS, f"unknown tool: {name}")
            )
            return
        self._send_json(200, rpc_result(request_id, result))

    # ------------------------------------------------------------------ #
    # MSSW mode
    # ------------------------------------------------------------------ #

    def _mssw_login(self, path: str) -> None:
        if path != MSSW_LOGIN_PATH:
            self._send_json(404, {"error": "not_found"})
            return
        body = self._read_body_text()
        if body is None:
            self._send_json(400, {"code": CODE_PARAMS_ERR, "msg": "unreadable body"})
            return
        if not self._verify_signature("POST", path, urlparse(self.path).query, body):
            self._send_json(401, {"code": CODE_AUTH_FAILED, "msg": "signature invalid"})
            return
        csrf = self.headers.get("x-csrftoken", "")
        if self.server.csrf_enabled and csrf not in self.server.csrf_tokens:
            self._send_json(403, {"code": CODE_AUTH_FAILED, "msg": "csrf token invalid"})
            return
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + self.server.session_ttl
        self.server.sessions[token] = (expires_at, "demo")
        if not csrf:
            csrf = secrets.token_urlsafe(16)
            self.server.csrf_tokens.add(csrf)
        headers = [
            ("Set-Cookie", self._session_cookie(token)),
            ("Set-Cookie", self._csrf_cookie(csrf)),
        ]
        self._send_json(200, {"code": CODE_SUCCESS, "authenticated": True, "user": "demo"}, headers)

    def _mssw_get(self, path: str) -> None:
        if path == "/__stats":
            self._send_json(200, {"counts": dict(self.server.request_counts)})
            return
        if path == "/__stats/reset":
            self.server.request_counts.clear()
            self._send_json(200, {"ok": True})
            return
        if path == "/":
            self._send_html(200, self._home_page())
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if path == MSSW_GET_TOKEN_PATH:
            csrf = secrets.token_urlsafe(16)
            self.server.csrf_tokens.add(csrf)
            self._send_json(200, {"code": CODE_SUCCESS}, [("Set-Cookie", self._csrf_cookie(csrf))])
            return
        if path == MSSW_HEALTH_PATH:
            if self._session_identity() is None:
                self._send_json(401, {"code": CODE_AUTH_FAILED, "msg": "no session"})
                return
            self._send_json(200, {"code": CODE_SUCCESS})
            return
        if path in (MSSW_PING_PATH, MSSW_TARGETS_PATH):
            identity = self._session_identity()
            if identity is None:
                self._send_json(401, {"code": CODE_AUTH_FAILED, "msg": "no session"})
                return
            if self.server.csrf_enabled and not self._csrf_valid():
                self._send_json(403, {"code": CODE_AUTH_FAILED, "msg": "csrf token invalid"})
                return
            self.server.request_counts[path] = self.server.request_counts.get(path, 0) + 1
            trace_id = self.headers.get(MSSW_TRACE_HEADER, "")
            payload: dict[str, Any] = {
                "code": CODE_SUCCESS,
                "user": identity[1],
                "traceId": trace_id,
                "path": path,
            }
            if path == MSSW_TARGETS_PATH:
                payload["items"] = [{"id": index, "name": f"target-{index}"} for index in range(1, 4)]
            self._send_json(200, payload)
            return
        self._send_json(404, {"error": "not_found"})

    def _verify_signature(self, method: str, path: str, query: str, body: str) -> bool:
        auth = self._parse_authorization(self.headers.get("Authorization", ""))
        if not auth:
            return False
        if auth.get("algorithm") != "HMAC-SHA256" or auth.get("Access") != self.server.ak:
            return False
        signed_names = [item for item in auth.get("SignedHeaders", "").split(";") if item]
        if not {"sign-date", "content-type"}.issubset(set(signed_names)):
            return False
        signed_headers = {name.lower(): self.headers.get(name, "") for name in signed_names}
        # The real adapter carries sign-date inside Authorization rather than as a
        # separate header; canonicalization must use that value.
        if not signed_headers.get("sign-date"):
            signed_headers["sign-date"] = auth.get("sign-date", "")
        timestamp = signed_headers.get("sign-date", "")
        if not timestamp:
            return False
        canonical = make_canonical_request(method, strip_gateway_prefix(path), query, signed_headers, body)
        sign_str = f"HMAC-SHA256\n{timestamp}\n{sha256_hex(canonical)}"
        expected = hmac.new(
            self.server.sk.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
        return hmac.compare_digest(expected, auth.get("Signature", "").upper())

    @staticmethod
    def _parse_authorization(value: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in value.split(","):
            key, separator, item = part.strip().partition("=")
            if separator and key:
                result[key.strip()] = item.strip()
        return result

    def _csrf_valid(self) -> bool:
        # Real MSSW/SOAR only require the header to be present; Runtime Guard
        # compat overrides it with a fresh UUID for business hosts.
        header_value = (
            self.headers.get("x-csrftoken")
            or self.headers.get("x-csrf-token")
            or ""
        )
        return bool(header_value)

    def _cookie_value(self, name: str) -> str:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return ""
        morsel = jar.get(name)
        return morsel.value if morsel is not None else ""

    def _csrf_cookie(self, value: str) -> str:
        return (
            f"csrf_token={value}; Path=/; HttpOnly; "
            f"SameSite=Lax; Max-Age={self.server.session_ttl}"
        )

    # ------------------------------------------------------------------ #
    # shared helpers
    # ------------------------------------------------------------------ #

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send_auth_json(self, payload: dict[str, Any]) -> None:
        if self._session_identity() is None:
            self._send_json(401, {"authenticated": False})
            return
        self._send_json(200, payload)

    def _send_business_error(self, code: int, msg: str) -> None:
        self._send_json(200, {"code": code, "msg": msg, "authenticated": False})

    def _read_body_text(self) -> str | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _read_json_body(self) -> dict[str, Any] | None:
        text = self._read_body_text()
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _valid_credentials(self, payload: dict[str, Any]) -> bool:
        return self.server.accounts.get(payload.get("username")) == payload.get("password")

    def _session_identity(self) -> tuple[str, str] | None:
        token = self._cookie_value(self.server.cookie_name)
        if not token:
            return None
        session = self.server.sessions.get(token)
        if session is None or session[0] <= time.time():
            return None
        return token, session[1]

    def _session_cookie(self, token: str) -> str:
        return (
            f"{self.server.cookie_name}={token}; Path=/; HttpOnly; "
            f"SameSite=Lax; Max-Age={self.server.session_ttl}"
        )

    def _home_page(self) -> str:
        status = "logged-in" if self._session_identity() else "logged-out"
        return (
            "<!doctype html><html><head><title>Fake Business Platform</title></head>"
            f"<body><main id=\"status\">{status}</main></body></html>"
        )

    def _send_json(
        self, status: int, payload: dict[str, Any], headers: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class UnknownMcpToolError(Exception):
    """Raised when tools/call names a tool that is not in the fake catalog."""


def rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def is_secret_header(name: str, configured: frozenset[str]) -> bool:
    lowered = name.lower()
    return lowered in SENSITIVE_HEADER_NAMES or lowered in configured


def constant_time_equals(received: str, expected: str) -> bool:
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8"))


def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def fake_targets() -> list[dict[str, Any]]:
    return [{"id": f"target-{index}", "name": f"target-{index}"} for index in range(1, 4)]


def call_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "platform.ping":
        return text_result("pong")
    if name == "platform.targets.list":
        return text_result(json.dumps({"targets": fake_targets()}, separators=(",", ":")))
    if name == "platform.echo":
        return text_result(str(arguments.get("message", "")))
    if name == "platform.targets.delete":
        target_id = str(arguments.get("id", ""))
        if not target_id:
            return {"content": [{"type": "text", "text": "missing target id"}], "isError": True}
        return text_result(json.dumps({"deleted": target_id}, separators=(",", ":")))
    if name == "platform.fail":
        return {"content": [{"type": "text", "text": "platform failure"}], "isError": True}
    raise UnknownMcpToolError(name)


def make_canonical_request(
    method: str,
    path: str,
    query: str,
    signed_headers: dict[str, str],
    body: str,
) -> str:
    canonical_query = "&".join(sorted(query.split("&"))) if query else ""
    keys = sorted(signed_headers)
    canon_headers = "".join(f"{key.lower()}:{signed_headers[key]}\n" for key in keys)
    result = f"{method}\n{path}\n"
    if canonical_query:
        result += f"{canonical_query}\n"
    result += f"{canon_headers}{';'.join(keys)}"
    if body:
        result += f"\n{sha256_hex(body)}"
    return result


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest().upper()


def strip_gateway_prefix(path: str) -> str:
    if not path.startswith(GATEWAY_PREFIX):
        return path
    rest = path[len(GATEWAY_PREFIX):]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else "/"


def iso_time(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fake business platform.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--platform", choices=("smoke_platform", "mssw"), default="smoke_platform")
    parser.add_argument("--cookie-name", default="fake_session")
    parser.add_argument("--session-ttl", type=int, default=900)
    parser.add_argument("--ak", default="test-ak")
    parser.add_argument("--sk", default="test-sk")
    parser.add_argument("--no-csrf", action="store_true", help="Disable CSRF validation in mssw mode")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--mcp-auth", choices=("none", "bearer", "headers"), default="none")
    parser.add_argument("--mcp-token", default="", help="Bearer token required by --mcp-auth bearer")
    parser.add_argument(
        "--mcp-header",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="Required static header for --mcp-auth headers; repeatable",
    )
    parser.add_argument("--mcp-path", default=MCP_DEFAULT_PATH)
    return parser


def parse_header_options(
    values: list[str] | None, parser: argparse.ArgumentParser,
) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in values or []:
        name, separator, value = raw.partition("=")
        name = name.strip()
        if not separator or not name:
            parser.error(f"--mcp-header must be NAME=VALUE, got {raw!r}")
        lowered = name.lower()
        if lowered in seen:
            parser.error(f"--mcp-header duplicate name {name!r}")
        seen.add(lowered)
        headers.append((name, value))
    return headers


def validate_mcp_options(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.mcp_auth == "bearer" and not args.mcp_token:
        parser.error("--mcp-auth bearer requires --mcp-token")
    if args.mcp_auth == "headers" and not args.mcp_headers:
        parser.error("--mcp-auth headers requires at least one --mcp-header NAME=VALUE")
    if not args.mcp_path.startswith("/"):
        parser.error("--mcp-path must start with '/'")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.mcp_headers = parse_header_options(args.mcp_header, parser)
    validate_mcp_options(args, parser)
    return args


def main() -> int:
    args = parse_args()
    server = FakeBusinessPlatform((args.host, args.port), Handler, args)
    host = "127.0.0.1" if args.host in ("", "0.0.0.0", "::") else args.host
    base_url = f"http://{host}:{server.server_port}"
    ready: dict[str, Any] = {
        "status": "ready",
        "baseUrl": base_url,
        "platform": args.platform,
        "mcpUrl": f"{base_url}{args.mcp_path}",
        "mcpAuth": args.mcp_auth,
        "mcpTools": [str(tool["name"]) for tool in MCP_TOOLS],
    }
    if args.platform == "mssw":
        ready.update({
            "ak": args.ak,
            "sk": args.sk,
            "csrfEnabled": not args.no_csrf,
            "sessionEndpoint": MSSW_LOGIN_PATH,
            "healthEndpoint": MSSW_HEALTH_PATH,
            "pingEndpoint": MSSW_PING_PATH,
        })
    print(json.dumps(ready), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
