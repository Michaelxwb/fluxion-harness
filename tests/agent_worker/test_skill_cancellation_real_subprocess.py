"""E-08: real executor, descendants, retained pipe and HTTP cancellation signal."""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest, SkillExecutionStatus

from tests.acceptance.runtime.conftest import _Server

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mode", ["cancel", "timeout", "exited-parent"])
async def test_e08_descendant_reclaimed_and_output_drain_bounded(tmp_path: Path, mode: str):
    ready, signalled, pid_file = (tmp_path / name for name in ("ready", "signalled", "pid"))
    child = tmp_path / "descendant.py"
    child.write_text(
        "import os, signal, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"signal.signal(signal.SIGTERM, lambda *_: open({str(signalled)!r}, 'w').write('term'))\n"
        f"open({str(ready)!r}, 'w').write('ready')\n"
        "time.sleep(60)\n"
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = "import subprocess, sys, time\n" + f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
    if mode != "exited-parent":
        source += "time.sleep(60)\n"
    (scripts / "main.py").write_text(source)
    cancel = asyncio.Event()
    app = FastAPI()

    @app.post("/cancel")
    async def cancel_over_http():
        # The HTTP thread cannot set an asyncio.Event owned by the executor loop directly.
        loop.call_soon_threadsafe(cancel.set)
        return {"cancel_requested": True, "remote_side_effects_stopped": False}

    loop = asyncio.get_running_loop()
    server = _Server(app)
    url = server.start()
    request = SkillExecutionRequest(
        ready_dir=tmp_path, input={}, env={}, timeout_sec=30 if mode == "cancel" else 1, cancel_event=cancel
    )
    started = time.monotonic()
    execution = asyncio.create_task(ScriptSkillExecutor().execute(request))
    try:
        deadline = loop.time() + 5
        while not ready.exists() and loop.time() < deadline:
            await asyncio.sleep(0.02)
        assert ready.exists()
        if mode == "cancel":
            async with httpx.AsyncClient() as client:
                response = await client.post(url + "/cancel")
                assert response.json() == {"cancel_requested": True, "remote_side_effects_stopped": False}
        result = await asyncio.wait_for(execution, 15)
        assert result.status is (
            SkillExecutionStatus.CANCELLED if mode == "cancel" else SkillExecutionStatus.TIMED_OUT
        )
        assert time.monotonic() - started < 15 and signalled.exists()
        pid = int(pid_file.read_text())
        state = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
        ).stdout.strip()
        assert not state or state.startswith("Z"), f"descendant {pid} still runs: {state}"
    finally:
        server.stop()
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except ProcessLookupError:
                pass  # An already reaped test descendant is the expected successful cleanup.
