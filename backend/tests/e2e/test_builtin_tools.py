from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.builtin_tools import (
    BuiltinToolConfig,
    _http_get,
    register_builtin_tools,
)
from fluxion.runtime.tools import ToolAuthorizationError, ToolResultStatus, ToolRuntime


@pytest.mark.asyncio
async def test_S_R15_builtin_tools_use_common_chain_and_enforce_file_allowlist(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "workspace"
    allowed_root.mkdir()
    (allowed_root / "note.txt").write_text("hello", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    context, _runtime = await runtime_context()
    tool_runtime = ToolRuntime()
    register_builtin_tools(
        tool_runtime,
        BuiltinToolConfig(allowed_roots=[allowed_root], write_approved=False),
    )

    allowed_tools = {
        "time.now",
        "calc.eval",
        "http.get",
        "file.read",
        "file.write",
        "file.list",
        "file.search",
    }
    common = {
        "user_grants": allowed_tools,
        "agent_allowlist": allowed_tools,
        "tenant_policy": allowed_tools,
    }
    now = await tool_runtime.call(context, "time.now", {}, **common)
    calc = await tool_runtime.call(context, "calc.eval", {"expression": "1 + 2 * 3"}, **common)
    read = await tool_runtime.call(
        context,
        "file.read",
        {"path": str(allowed_root / "note.txt")},
        **common,
    )

    assert now.status is ToolResultStatus.COMPLETED
    assert now.result is not None
    assert now.result["timezone"] == "UTC"
    assert calc.result == {"value": 7}
    assert read.result == {"content": "hello"}
    assert tool_runtime.descriptor("time.now").external_dependency is False
    assert tool_runtime.descriptor("calc.eval").external_dependency is False

    with pytest.raises(ToolAuthorizationError) as outside_error:
        await tool_runtime.call(context, "file.read", {"path": str(outside)}, **common)
    assert outside_error.value.code == "path_not_allowed"

    with pytest.raises(ToolAuthorizationError) as write_error:
        await tool_runtime.call(
            context,
            "file.write",
            {"path": str(allowed_root / "note.txt"), "content": "new"},
            **common,
        )
    assert write_error.value.code == "approval_required"


async def _granted_tool_runtime(
    tmp_path: Path | None,
) -> tuple[ToolRuntime, dict[str, set[str]]]:
    tool_runtime = ToolRuntime()
    register_builtin_tools(
        tool_runtime,
        BuiltinToolConfig(
            allowed_roots=[tmp_path] if tmp_path is not None else [],
            write_approved=False,
        ),
    )
    granted = {"http.get", "calc.eval"}
    common = {
        "user_grants": granted,
        "agent_allowlist": granted,
        "tenant_policy": granted,
    }
    return tool_runtime, common


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
    ],
)
async def test_E_R16_http_get_rejects_non_http_schemes(tmp_path: Path, url: str) -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path)
    context, _runtime = await runtime_context()
    with pytest.raises(ToolAuthorizationError) as exc:
        await tool_runtime.call(context, "http.get", {"url": url}, **common)
    assert exc.value.code == "scheme_not_allowed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://localhost:8000/health",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/config",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/health",
        "http://[fc00::1]/admin",
    ],
)
async def test_E_R16_http_get_rejects_loopback_and_private_hosts(
    tmp_path: Path, url: str
) -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path)
    context, _runtime = await runtime_context()
    with pytest.raises(ToolAuthorizationError) as exc:
        await tool_runtime.call(context, "http.get", {"url": url}, **common)
    assert exc.value.code == "host_not_allowed"


def test_S5_http_get_returns_redirect_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5 回归：http.get 不跟随重定向——3xx 响应原样返回（status=302），
    不取回 Location 目标内容（否则 SSRF：校验过的公网域名 302 跳到内网）。
    http.client 本身不跟随重定向；校验逻辑由 loopback/private 测试覆盖，
    这里把解析结果指向本地测试服务以走通连接。"""

    class _RedirectServer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/secret")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"SECRET-BODY")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _RedirectServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "fluxion.runtime.builtin_tools._resolve_public_host",
        lambda _host, _url, _port: ["127.0.0.1"],
    )
    try:
        result = _http_get(
            None,  # type: ignore[arg-type] — _context 未被 http.get 使用
            {"url": f"http://example.com:{server.server_port}/redir"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["status"] == 302
    assert result["body"] != "SECRET-BODY"


def test_S5_http_get_resolves_hostname_once_and_pins_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5：DNS rebinding 封堵——hostname 只解析一次（校验），连接钉扎到解析出
    的 IP 字面量，不再按 hostname 二次解析。校验与连接之间 DNS 变化无法把
    连接引到内网。"""
    hostname_resolutions: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if str(host) == "example.com":
            hostname_resolutions.append(str(host))
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]
        return real_getaddrinfo(host, port, *args, **kwargs)

    connect_targets: list[object] = []

    def fake_create_connection(address, *args, **kwargs):
        connect_targets.append(address)
        raise RuntimeError("stop")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    with pytest.raises(RuntimeError, match="stop"):
        _http_get(None, {"url": "http://example.com/path"})  # type: ignore[arg-type]

    assert hostname_resolutions == ["example.com"]
    assert connect_targets == [("93.184.216.34", 80)]


@pytest.mark.asyncio
async def test_E_R16_calc_eval_rejects_division_by_zero_and_boolean() -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path=None)
    context, _runtime = await runtime_context()
    with pytest.raises(ValueError, match="division by zero"):
        await tool_runtime.call(context, "calc.eval", {"expression": "1 / 0"}, **common)
    with pytest.raises(ValueError, match="unsupported expression"):
        await tool_runtime.call(context, "calc.eval", {"expression": "True + 1"}, **common)
