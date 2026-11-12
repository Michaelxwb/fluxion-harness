"""Real session broker, Redis, HTTP cancellation and Skill process supervision."""

import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from muad_agent_core.sandbox import DevUnsafeRunner, build_sandbox_runner
from muad_agent_core.skill import SandboxedSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk.platform_transport import PlatformBudgetScope
from muad_platform_sdk.request_budget import create_budget_controller

from tests.sdk.test_egress_budget_lifecycle import _process_exists
from tests.sdk.test_mss_sessions import platform_server as platform_server_fixture
from tests.sdk.test_platform_sliding_session import cache as cache_fixture
from tests.sdk.test_platform_sliding_session import execution_context as execution_context_fixture

platform_server = platform_server_fixture
cache = cache_fixture
execution_context = execution_context_fixture
pytestmark = pytest.mark.integration


def _stop_executor(native_bwrap: bool) -> SandboxedSkillExecutor:
    if native_bwrap:
        runner = build_sandbox_runner(mode="bwrap", env="test")
        assert runner.name == "bwrap"
        return SandboxedSkillExecutor(runner=runner)
    return SandboxedSkillExecutor(runner=DevUnsafeRunner(), dev_unsafe=True)


def _pending_processes(pids: Path, native_bwrap: bool) -> list[int]:
    if not native_bwrap:
        return [pid for pid in json.loads(pids.read_text()) if _process_exists(pid)]
    pending = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = path.read_bytes()
        except (ProcessLookupError, FileNotFoundError):
            continue
        if b"/skill/scripts/main.py" in command or b"/session_cli.py" in command:
            pending.append(int(path.parent.name))
    return pending


def _slow_health(server, marker: Path):
    base = server.RequestHandlerClass
    closed = threading.Event()
    calls = []

    class SlowHealth(base):
        def do_GET(self):
            calls.append(self.path)
            if self.path != "/v1/rtt":
                return super().do_GET()
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.end_headers()
            self.wfile.write(b"{")
            self.wfile.flush()
            marker.write_text("started", encoding="utf-8")
            self.connection.settimeout(6)
            try:
                if self.connection.recv(1) == b"":
                    closed.set()
            except (ConnectionResetError, BrokenPipeError):
                closed.set()
            except TimeoutError:
                pass  # The test asserts closed, so a timeout fails visibly.

        def do_POST(self):
            calls.append(self.path)
            return super().do_POST()

    server.RequestHandlerClass = SlowHealth
    return closed, calls


def _concurrent_cli_script(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: platform_server\ndescription: probe\nplatforms: [platform_server]\n---\nprobe\n",
        encoding="utf-8",
    )
    (root / "scripts/main.py").write_text(
        "import json, os, pathlib, subprocess, sys, time\n"
        "args = json.load(sys.stdin)\n"
        "cmd = ['session-manager', 'get-state', '--skill-name', 'platform_server']\n"
        "pending = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "pathlib.Path(args['pids']).write_text(json.dumps([os.getpid(), pending.pid]))\n"
        "while not pathlib.Path(args['marker']).exists(): time.sleep(.01)\n"
        "subprocess.run(cmd, capture_output=True)\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    return root


async def test_budget_stop_closes_broker_http_and_reaps_cli_tree(
    tmp_path, platform_server, cache, execution_context, caplog,
):
    server, _, _ = platform_server
    native = os.environ.get("MUAD_EGRESS_TEST_NATIVE_BWRAP") == "1"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker, pids = workspace / "health-started", workspace / "pids.json"
    closed, calls = _slow_health(server, marker)
    controller = create_budget_controller(
        resolve_egress_budget({"execution": {"max_requests": 3}}), segment_id="stop-probe",
    )
    assert controller is not None
    invocation = controller.invocation("platform_server", "stop-invocation")
    scope = PlatformBudgetScope(controller, invocation)
    executor = _stop_executor(native)
    with pytest.raises(SkillExecutionError) as caught:
        unexpected = await asyncio.wait_for(executor.execute(SkillExecutionRequest(
            ready_dir=_concurrent_cli_script(tmp_path / "package"), source="USER", timeout_sec=12,
            input={
                "marker": "/workspace/health-started" if native else str(marker),
                "pids": "/workspace/pids.json" if native else str(pids),
            },
            workspace_dir=workspace,
            platform_context=replace(execution_context, budget_scope=scope),
            budget_exhausted_event=invocation.exhausted,
        )), timeout=15)
        pytest.fail(
            f"expected budget stop; sandbox returned {unexpected.status}: {unexpected.stderr[-1000:]}"
        )
    assert caught.value.code == "LOCAL_BUDGET_EXHAUSTED"
    assert caught.value.retryable is False
    assert await asyncio.to_thread(closed.wait, 3), "HTTP transport remained open after invocation stop"
    assert len(calls) == 3 and calls[-1] == "/v1/rtt"
    assert controller.used == 3 and controller.inflight == 0
    assert invocation.exhausted.is_set()
    assert "Unhandled exception in client_connected_cb" not in caplog.text
    assert "session_broker_internal_error type=LocalRateLimitError" not in caplog.text
    async with asyncio.timeout(3):
        while _pending_processes(pids, native):
            await asyncio.sleep(.01)
    redis, _, manager, _ = cache
    assert len(server.sessions) == 1  # A completed login is not rolled back.
    assert await redis.scard(manager.index) == 1
