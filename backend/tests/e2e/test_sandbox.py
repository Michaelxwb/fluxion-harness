from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.sandbox import (
    BubblewrapSandboxBackend,
    DevSandboxBackend,
    RecordingSandboxBackend,
    SandboxBackendRegistry,
    SandboxExecBackend,
    SandboxRequest,
    SandboxUnavailableError,
)
from fluxion.runtime.tools import ToolAuthorizationError, ToolRuntime


@pytest.mark.asyncio
async def test_S_R16_run_command_uses_sandbox_defaults_and_fails_closed_without_backend() -> None:
    context, _runtime = await runtime_context()
    sandbox = RecordingSandboxBackend(stdout="ok")
    tool_runtime = ToolRuntime()
    register_builtin_tools(
        tool_runtime,
        BuiltinToolConfig(sandbox_backend=sandbox, allow_run_command=True),
    )
    common = {
        "user_grants": {"run_command", "code.exec"},
        "agent_allowlist": {"run_command", "code.exec"},
        "tenant_policy": {"run_command", "code.exec"},
    }

    result = await tool_runtime.call(
        context,
        "run_command",
        {"command": ["echo", "ok"], "timeout_ms": 100},
        **common,
    )

    assert result.result == {"stdout": "ok", "stderr": "", "exit_code": 0}
    assert sandbox.requests[0].network_enabled is False
    assert sandbox.requests[0].root_read_only is True
    assert sandbox.requests[0].timeout_ms == 100

    no_sandbox_runtime = ToolRuntime()
    register_builtin_tools(
        no_sandbox_runtime,
        BuiltinToolConfig(sandbox_backend=None, allow_run_command=True),
    )
    with pytest.raises(ToolAuthorizationError) as exc_info:
        await no_sandbox_runtime.call(
            context,
            "run_command",
            {"command": ["echo", "blocked"]},
            **common,
        )
    assert exc_info.value.code == "sandbox_unavailable"
    assert any(event.name == "sandbox.unavailable" for event in context.trace)

    with pytest.raises(ToolAuthorizationError) as code_exc_info:
        await no_sandbox_runtime.call(
            context,
            "code.exec",
            {"code": "print('blocked')"},
            **common,
        )
    assert code_exc_info.value.code == "sandbox_unavailable"


def test_S_R16_platform_matrix_resolves_native_backends_and_fails_closed() -> None:
    registry = SandboxBackendRegistry()

    linux = registry.resolve(platform_system="Linux", bwrap_path="/usr/bin/bwrap")
    assert isinstance(linux, BubblewrapSandboxBackend)

    darwin = registry.resolve(platform_system="Darwin", bwrap_path=None)
    assert isinstance(darwin, SandboxExecBackend)

    with pytest.raises(SandboxUnavailableError):
        registry.resolve(platform_system="NoNativeOS", bwrap_path=None)

    dev = registry.resolve(platform_system="NoNativeOS", bwrap_path=None, allow_dev_fallback=True)
    assert isinstance(dev, DevSandboxBackend)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec 是 macOS 原生后端")
async def test_S_R16_sandbox_exec_blocks_write_outside_tmp_on_macos() -> None:
    # RULE-20 只读根：非 /tmp 路径写入必须被 Seatbelt 拒绝，且宿主侧无残留。
    backend = SandboxExecBackend()
    result = await backend.run(
        SandboxRequest(command=["bash", "-c", "echo hi > .sbx-blocked.txt"], timeout_ms=5000)
    )
    assert result.exit_code != 0
    assert "Operation not permitted" in result.stderr


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec 是 macOS 原生后端")
async def test_S_R16_sandbox_exec_blocks_network_on_macos() -> None:
    # RULE-20 默认无网络：宿主可达的本地服务在 sandbox-exec 内必须不可达。
    server, port = _spawn_http_server()
    url = f"http://127.0.0.1:{port}/"
    try:
        host_result = await DevSandboxBackend().run(
            SandboxRequest(command=["curl", "-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}", url], timeout_ms=5000)
        )
        assert host_result.stdout.strip() == "200"

        sandboxed = await SandboxExecBackend().run(
            SandboxRequest(command=["curl", "-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}", url], timeout_ms=5000)
        )
        assert sandboxed.exit_code != 0
    finally:
        server.shutdown()


def test_S_R16_bubblewrap_builds_isolation_argv() -> None:
    # FEAT-21：Linux bwrap 后端 argv 构造（本机可验证，真实隔离在 Linux 环境验证）。
    backend = BubblewrapSandboxBackend("/usr/bin/bwrap")
    argv = backend.build_argv(SandboxRequest(command=["echo", "hi"], timeout_ms=100))
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--ro-bind" in argv
    assert "--tmpfs" in argv
    assert "--proc" in argv
    assert "--share-net" not in argv  # 默认无网络（--unshare-all 已断网）
    assert argv[-3:] == ["--", "echo", "hi"]

    net_argv = backend.build_argv(
        SandboxRequest(command=["curl", "example.com"], network_enabled=True)
    )
    assert "--share-net" in net_argv  # 显式开启网络


class _SilentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *args: object) -> None:
        del _format, args


def _spawn_http_server() -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", 0), _SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]
