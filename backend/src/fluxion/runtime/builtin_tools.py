from __future__ import annotations

import ast
import ipaddress
import operator
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.client import HTTPResponse
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

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


def _http_get(_context: RuntimeContext, arguments: dict[str, object]) -> dict[str, object]:
    url = _required_string(arguments, "url")
    raw_timeout = arguments.get("timeout_seconds", 5.0)
    if not isinstance(raw_timeout, int | float):
        raise TypeError("timeout_seconds must be numeric")
    timeout = float(raw_timeout)
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolAuthorizationError(
            "scheme_not_allowed", f"url scheme {parsed.scheme!r} is not allowed"
        )
    _assert_public_host(parsed.hostname, url)
    with urlopen(url, timeout=timeout) as response:
        body = _read_limited(response).decode("utf-8", errors="replace")
        status = int(getattr(response, "status", 200))
    return {"status": status, "body": body}


def _read_limited(response: HTTPResponse) -> bytes:
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


def _assert_public_host(hostname: str | None, url: str) -> None:
    if not hostname:
        raise ToolAuthorizationError("invalid_url", "url is missing a host")
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ToolAuthorizationError(
            "dns_resolution_failed", f"could not resolve host {hostname!r}"
        ) from exc
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
