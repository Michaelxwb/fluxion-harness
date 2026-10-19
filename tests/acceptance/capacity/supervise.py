"""E2E 单 Pod 重启看护进程：起一个真实 uvicorn 单元，并跟随父进程生命周期退出。

用途（TASK-116 的 `e2e/tests/webchat/capacity-recovery.spec.ts`）：Playwright 的 `webServer`
只在运行前起进程、**不会在运行中重启**，所以「单 Console/Gateway 重启」必须由用例自己做真实
进程替换。本模块把「重启后的单元」包一层看护：

- 子进程是**真实 uvicorn**（与 `stack.py` 的 `UNIT_MODULES` 同源，`PYTHONPATH=仓库根` 口径）；
- 就绪探测走真实 `GET /healthz`，就绪后往 stdout 打一行 `{"ready": true, "pid": ...}`；
- 父进程（Playwright worker）消失后 1 秒内杀掉子进程**整组**再退出 —— 即使 worker 被
  SIGKILL，也不会留下孤儿 uvicorn（不会污染后续套件/端口）。

用法（由 spec 以真实子进程方式调用）：

    python -m tests.acceptance.capacity.supervise --unit im-gateway --port 10307 --parent-pid 4242

失败一律显式：未知单元、子进程提前退出、就绪超时都打印错误并回非 0 退出码。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
READY_TIMEOUT_SEC = 90.0
PARENT_POLL_SEC = 0.5
HEALTH_PATH = "/healthz"
UNIT_MODULES: dict[str, str] = {
    "console-platform": "muad_console_platform.main:app",
    "agent-runtime": "muad_agent_runtime.main:app",
    "agent-worker": "muad_agent_worker.main:app",
    "im-gateway": "muad_im_gateway.main:app",
}


def _spawn_unit(unit: str, port: int) -> subprocess.Popen[bytes]:
    module = UNIT_MODULES[unit]
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        start_new_session=True,
    )


def _health_ok(port: int, *, timeout_sec: float) -> bool:
    import httpx

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2.0, trust_env=False)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return True
        time.sleep(0.1)
    return False


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return


def _parent_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.acceptance.capacity.supervise")
    parser.add_argument("--unit", required=True, choices=sorted(UNIT_MODULES))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args(argv)

    process = _spawn_unit(args.unit, args.port)
    if not _health_ok(args.port, timeout_sec=READY_TIMEOUT_SEC):
        _kill_group(process)
        print(
            json.dumps({"ready": False, "unit": args.unit, "port": args.port, "error": "health_timeout"}),
            flush=True,
        )
        return 1
    print(
        json.dumps({"ready": True, "unit": args.unit, "port": args.port, "pid": process.pid}),
        flush=True,
    )
    while True:
        if process.poll() is not None:
            print(json.dumps({"exit": process.returncode, "unit": args.unit}), flush=True)
            return 1
        if not _parent_alive(args.parent_pid):
            _kill_group(process)
            return 0
        time.sleep(PARENT_POLL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
