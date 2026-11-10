"""真实子进程与进程内预算段生命周期（TASK-005 E-02/E-03）。"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_contracts.egress_budget import EffectiveEgressBudget, RequestLimits, SkillBudget
from muad_platform_sdk.request_budget import AttemptContext, LocalRateLimitError, create_budget_controller


def _policy(total: int) -> EffectiveEgressBudget:
    return EffectiveEgressBudget(
        execution=RequestLimits(max_requests=total),
        skills={"probe": SkillBudget(total=RequestLimits(max_requests=total))},
    )


def _script(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")


async def test_budget_denial_signals_parent_and_kills_skill_group(tmp_path: Path) -> None:
    _script(tmp_path)
    controller = create_budget_controller(_policy(1), segment_id="segment-a")
    assert controller is not None
    invocation = controller.invocation("probe", "invocation-a")
    context = AttemptContext(platform_key="mss", method="GET", path="/assets")
    async with controller.attempt(context) as lease:
        lease.outcome = "success"
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(
            AttemptContext(platform_key="mss", method="GET", path="/assets", invocation=invocation)
        ):
            pass
    with pytest.raises(SkillExecutionError, match="LOCAL_BUDGET_EXHAUSTED"):
        await ScriptSkillExecutor().execute(
            SkillExecutionRequest(
                ready_dir=tmp_path,
                input={},
                timeout_sec=10,
                budget_exhausted_event=invocation.exhausted,
            )
        )


async def test_new_process_segment_starts_a_new_local_counter() -> None:
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = payload["method"]
            calls.append(method)
            body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}).encode()
            self.send_response(429 if method == "tools/call" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "7")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/mcp"
        first = await _runtime_process_probe(endpoint)
        second = await _runtime_process_probe(endpoint)
        assert first["pid"] != second["pid"]
        assert first["segment"] != second["segment"]
        for result in (first, second):
            assert result["used"] == 3
            assert result["origin"] == "upstream_http"
            assert result["status"] == 429 and result["retry_after"] == "7"
            assert result["second_origin"] == "LOCAL_BUDGET"
        assert calls == ["initialize", "notifications/initialized", "tools/call"] * 2
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()


async def _runtime_process_probe(endpoint: str) -> dict[str, object]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.sdk.egress_process_probe", endpoint,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        assert process.returncode == 0, stderr.decode()
        return json.loads(stdout.splitlines()[-1])
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_sandbox_budget_denial_reaps_running_process_group(tmp_path: Path) -> None:
    from muad_agent_core.sandbox import DevUnsafeRunner
    from muad_agent_core.skill import SandboxedSkillExecutor

    pid_file = _process_tree_script(tmp_path)
    controller = create_budget_controller(_policy(1), segment_id="sandbox-segment")
    assert controller is not None
    invocation = controller.invocation("probe", "running-invocation")
    context = AttemptContext(platform_key="mss", method="GET", path="/assets", invocation=invocation)
    cancel = asyncio.Event()
    task = asyncio.create_task(SandboxedSkillExecutor(
        runner=DevUnsafeRunner(), dev_unsafe=True,
    ).execute(SkillExecutionRequest(
        ready_dir=tmp_path, input={"pid_file": str(pid_file)}, source="USER",
        timeout_sec=15, cancel_event=cancel, budget_exhausted_event=invocation.exhausted,
    )))
    try:
        async with asyncio.timeout(5):
            while not pid_file.exists():
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        pids = [int(value) for value in pid_file.read_text().split()]
        async with controller.attempt(context):
            assert not task.done()  # 第 N 次允许完成，不能提前终止脚本。
        assert not invocation.exhausted.is_set()
        with pytest.raises(LocalRateLimitError):
            async with controller.attempt(context):
                pytest.fail("denied request was sent")
        with pytest.raises(SkillExecutionError) as caught:
            await asyncio.wait_for(asyncio.shield(task), timeout=4)
        assert caught.value.code == "LOCAL_BUDGET_EXHAUSTED"
        assert caught.value.retryable is False
        async with asyncio.timeout(3):
            while any(_process_exists(pid) for pid in pids):
                await asyncio.sleep(0.01)
    finally:
        cancel.set()
        await asyncio.gather(task, return_exceptions=True)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _process_tree_script(root: Path) -> Path:
    _script(root)
    (root / "SKILL.md").write_text("# probe\n", encoding="utf-8")
    (root / "scripts/main.py").write_text(
        "import json, os, pathlib, signal, subprocess, sys, time\n"
        "p = json.load(sys.stdin)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(p['pid_file']).write_text(f'{os.getpid()} {child.pid}')\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    return root / "pids"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_sandbox_budget_watcher_preserves_success_and_user_cancel(
    tmp_path: Path, cancelled: bool,
) -> None:
    from muad_agent_core.sandbox import DevUnsafeRunner
    from muad_agent_core.skill import SandboxedSkillExecutor, SkillExecutionStatus

    _script(tmp_path)
    (tmp_path / "SKILL.md").write_text("# probe\n", encoding="utf-8")
    (tmp_path / "scripts/main.py").write_text('print("{}")\n', encoding="utf-8")
    cancel = asyncio.Event()
    if cancelled:
        cancel.set()
    result = await SandboxedSkillExecutor(runner=DevUnsafeRunner(), dev_unsafe=True).execute(
        SkillExecutionRequest(
            ready_dir=tmp_path, input={}, source="USER", timeout_sec=5,
            cancel_event=cancel, budget_exhausted_event=asyncio.Event(),
        )
    )
    assert result.status == (SkillExecutionStatus.CANCELLED if cancelled else SkillExecutionStatus.SUCCEEDED)
