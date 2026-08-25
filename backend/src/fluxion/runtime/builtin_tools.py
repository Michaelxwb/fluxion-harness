from __future__ import annotations

import ast
import http.client
import ipaddress
import operator
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.sandbox import SandboxBackend, SandboxRequest, SandboxUnavailableError
from fluxion.runtime.tools import (
    ToolAuthorizationError,
    ToolDescriptor,
    ToolRuntime,
    ToolRuntimeError,
)


@dataclass(frozen=True, slots=True)
class BuiltinToolConfig:
    allowed_roots: list[Path] = field(default_factory=list)
    write_approved: bool = False
    sandbox_backend: SandboxBackend | None = None
    allow_run_command: bool = False


def register_builtin_tools(runtime: ToolRuntime, config: BuiltinToolConfig) -> None:
    allowed_roots = tuple(root.resolve() for root in config.allowed_roots)
    runtime.register(
        ToolDescriptor(
            tool_id="time.now",
            capability_id="builtin.time",
            name="time.now",
            external_dependency=False,
        ),
        _time_now,
    )
    runtime.register(
        ToolDescriptor(
            tool_id="calc.eval",
            capability_id="builtin.calc",
            name="calc.eval",
            external_dependency=False,
        ),
        _calc_eval,
    )
    runtime.register(
        ToolDescriptor(tool_id="http.get", capability_id="builtin.http", name="http.get"),
        _http_get,
    )
    runtime.register(
        ToolDescriptor(tool_id="file.read", capability_id="builtin.file", name="file.read"),
        lambda _ctx, args: _read_file(args, allowed_roots),
    )
    runtime.register(
        ToolDescriptor(tool_id="file.list", capability_id="builtin.file", name="file.list"),
        lambda _ctx, args: _list_dir(args, allowed_roots),
    )
    runtime.register(
        ToolDescriptor(tool_id="file.search", capability_id="builtin.file", name="file.search"),
        lambda _ctx, args: _search_files(args, allowed_roots),
    )
    runtime.register(
        ToolDescriptor(
            tool_id="file.write",
            capability_id="builtin.file",
            name="file.write",
            risk_level="high",
        ),
        lambda _ctx, args: _write_file(args, allowed_roots, config.write_approved),
    )
    runtime.register(
        ToolDescriptor(
            tool_id="run_command",
            capability_id="builtin.sandbox",
            name="run_command",
            risk_level="high",
        ),
        lambda ctx, args: _run_command(ctx, args, config),
    )
    runtime.register(
        ToolDescriptor(
            tool_id="code.exec",
            capability_id="builtin.sandbox",
            name="code.exec",
            risk_level="high",
        ),
        lambda ctx, args: _run_code(ctx, args, config),
    )


def _time_now(_context: RuntimeContext, _arguments: dict[str, object]) -> dict[str, object]:
    return {"iso": datetime.now(UTC).isoformat(), "timezone": "UTC"}


def _calc_eval(_context: RuntimeContext, arguments: dict[str, object]) -> dict[str, object]:
    expression = _required_string(arguments, "expression")
    return {"value": _eval_expr(ast.parse(expression, mode="eval").body)}


_MAX_HTTP_RESPONSE_BYTES = 1_000_000
# 模型可控的 timeout 必须有硬上限：否则一次指向黑洞地址的 http.get
# 会长时间占用 worker 线程（配合 ToolRuntime 的 to_thread 卸载）。
_MAX_HTTP_TIMEOUT_SECONDS = 30.0


def _connect_to_pinned_ips(
    pinned_ips: list[str], port: int, timeout: float | None
) -> socket.socket:
    """逐个连接已校验的公网 IP 字面量，保持多 IP 容错（首个可达者返回）。

    传入的是 IP 字面量而非 hostname：socket.create_connection 对字面量只做
    解析、不做 DNS 查询——校验与连接之间无第二次 DNS 解析（封堵 rebinding）。
    """
    last_error: OSError | None = None
    for ip in pinned_ips:
        try:
            return socket.create_connection((ip, port), timeout)
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("no public address to connect")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """把 TCP 连接钉扎到已解析并校验的公网 IP，而非再次按 host 解析。

    http.client 默认按 host 重新 getaddrinfo——与 SSRF 校验是两次独立解析，
    之间 DNS 可被 rebind 到内网。这里连接到解析后的 IP 字面量；Host 头仍用
    原始 hostname（putrequest 自动设置）。
    """

    def __init__(
        self,
        host: str,
        pinned_ips: list[str],
        port: int | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self._pinned_ips = pinned_ips
        super().__init__(host, port, timeout=timeout)

    def connect(self) -> None:
        self.sock = _connect_to_pinned_ips(self._pinned_ips, self.port, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """同上，但 TLS 的 SNI / 证书校验用原始 hostname（server_hostname=self.host）。"""

    def __init__(
        self,
        host: str,
        pinned_ips: list[str],
        port: int | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self._pinned_ips = pinned_ips
        super().__init__(host, port, timeout=timeout)
        # 默认校验上下文（证书校验 + hostname 校验），与 HTTPSConnection 默认一致。
        self._ssl_context = ssl.create_default_context()

    def connect(self) -> None:
        self.sock = _connect_to_pinned_ips(self._pinned_ips, self.port, self.timeout)
        self.sock = self._ssl_context.wrap_socket(self.sock, server_hostname=self.host)


def _http_get(_context: RuntimeContext, arguments: dict[str, object]) -> dict[str, object]:
    url = _required_string(arguments, "url")
    raw_timeout = arguments.get("timeout_seconds", 5.0)
    if not isinstance(raw_timeout, int | float):
        raise TypeError("timeout_seconds must be numeric")
    timeout = min(max(float(raw_timeout), 0.1), _MAX_HTTP_TIMEOUT_SECONDS)
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolAuthorizationError(
            "scheme_not_allowed", f"url scheme {parsed.scheme!r} is not allowed"
        )
    hostname = parsed.hostname
    if not hostname:
        raise ToolAuthorizationError("invalid_url", "url is missing a host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # 解析并校验公网 IP（仅此一次），连接钉扎到这些 IP 字面量——封堵 DNS rebinding。
    pinned_ips = _resolve_public_host(hostname, url, port)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = _PinnedHTTPSConnection(hostname, pinned_ips, port=port, timeout=timeout)
    else:
        connection = _PinnedHTTPConnection(hostname, pinned_ips, port=port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"User-Agent": "fluxion-http-get/1.0"})
        response = connection.getresponse()
        status = response.status
        body = _read_limited(response).decode("utf-8", errors="replace")
    finally:
        connection.close()
    return {"status": status, "body": body}


def _read_limited(response: http.client.HTTPResponse) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_HTTP_RESPONSE_BYTES:
            raise ToolRuntimeError("http response exceeds 1MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _resolve_public_host(hostname: str, url: str, port: int) -> list[str]:
    """解析 hostname 并校验所有结果均为公网地址，返回公网 IP 字面量列表。

    与 _PinnedHTTPConnection/_PinnedHTTPSConnection 配对：解析只发生这一次，
    连接用返回的 IP 字面量，不再二次按 hostname 解析（封堵 DNS rebinding）。
    任一解析结果落到 loopback/private/link-local 等非公网段即拒绝。
    """
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ToolAuthorizationError(
            "dns_resolution_failed", f"could not resolve host {hostname!r}"
        ) from exc
    public_ips: list[str] = []
    for _family, _socket_type, _protocol, _canonname, sockaddr in infos:
        try:
            address = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
        ):
            raise ToolAuthorizationError(
                "host_not_allowed", f"url {url} resolves to non-public address {address}"
            )
        public_ips.append(str(address))
    if not public_ips:
        raise ToolAuthorizationError(
            "dns_resolution_failed", f"could not resolve host {hostname!r}"
        )
    return public_ips


def _read_file(arguments: Mapping[str, object], allowed_roots: tuple[Path, ...]) -> dict[str, object]:
    path = _allowed_path(_required_string(arguments, "path"), allowed_roots)
    return {"content": path.read_text(encoding="utf-8")}


def _list_dir(arguments: Mapping[str, object], allowed_roots: tuple[Path, ...]) -> dict[str, object]:
    path = _allowed_path(_required_string(arguments, "path"), allowed_roots)
    return {"entries": sorted(child.name for child in path.iterdir())}


def _search_files(arguments: Mapping[str, object], allowed_roots: tuple[Path, ...]) -> dict[str, object]:
    root = _allowed_path(str(arguments.get("path", allowed_roots[0] if allowed_roots else ".")), allowed_roots)
    pattern = str(arguments.get("pattern", "*"))
    matches = [str(path) for path in sorted(root.rglob(pattern)) if path.is_file()]
    return {"matches": matches}


def _write_file(
    arguments: Mapping[str, object],
    allowed_roots: tuple[Path, ...],
    approved: bool,
) -> dict[str, object]:
    if not approved:
        raise ToolAuthorizationError("approval_required", "file.write requires approval")
    path = _allowed_path(_required_string(arguments, "path"), allowed_roots)
    content = _required_string(arguments, "content")
    path.write_text(content, encoding="utf-8")
    return {"written": True}


async def _run_command(
    context: RuntimeContext,
    arguments: dict[str, object],
    config: BuiltinToolConfig,
) -> dict[str, object]:
    if not config.allow_run_command or config.sandbox_backend is None:
        context.emit("sandbox.unavailable", {"tool_id": "run_command"})
        raise ToolAuthorizationError("sandbox_unavailable", "run_command requires SandboxBackend")
    command = arguments.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ValueError("command must be list[str]")
    timeout_ms = arguments.get("timeout_ms", 1000)
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive int")
    cwd = arguments.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be string")
    try:
        result = await config.sandbox_backend.run(
            SandboxRequest(
                command=command,
                cwd=cwd,
                timeout_ms=timeout_ms,
                network_enabled=False,
                root_read_only=True,
            )
        )
    except SandboxUnavailableError as exc:
        context.emit("sandbox.unavailable", {"tool_id": "run_command"})
        raise ToolAuthorizationError("sandbox_unavailable", str(exc)) from exc
    return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}


async def _run_code(
    context: RuntimeContext,
    arguments: dict[str, object],
    config: BuiltinToolConfig,
) -> dict[str, object]:
    language = str(arguments.get("language", "python"))
    if language != "python":
        raise ValueError("only python code.exec is supported")
    code = _required_string(arguments, "code")
    timeout_ms = arguments.get("timeout_ms", 1000)
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive int")
    return await _run_command(
        context,
        {"command": ["python3", "-c", code], "timeout_ms": timeout_ms},
        config,
    )


def _allowed_path(raw_path: str, allowed_roots: tuple[Path, ...]) -> Path:
    path = Path(raw_path).resolve()
    for root in allowed_roots:
        if path == root or root in path.parents:
            return path
    raise ToolAuthorizationError("path_not_allowed", f"{raw_path} is outside allowlist")


def _required_string(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _eval_expr(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            # calc 工具对非法表达式统一抛 ValueError（含类型不符），测试契约依赖该类型
            raise ValueError("unsupported expression")  # noqa: TRY004
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("unsupported operator")
        try:
            value = op(float(_eval_expr(node.left)), float(_eval_expr(node.right)))
        except ZeroDivisionError as exc:
            raise ValueError("division by zero") from exc
        return int(value) if value.is_integer() else value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _eval_expr(node.operand)
        return -value
    raise ValueError("unsupported expression")
