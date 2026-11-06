#!/usr/bin/env python3
"""[E-02] 在真实 bwrap 内回收完整进程树的探针。

这个脚本既能在原生 Linux（已有 bwrap）里跑，也能被 ``test_e_02.py`` 挂进
``fx-sandbox-test:local`` 之类的测试镜像里跑。它只依赖 agent-core 的 sandbox 层
（stdlib-only）和容器里已有的 Python，不 mock 任何进程/信号边界。

覆盖：

1. 超时：脚本自身、同进程组的后台子进程、``setsid`` + 双重 fork 的 daemon 全部回收；
2. 取消：同样回收完整后代，并返回 ``SANDBOX_CANCELLED``；
3. 父进程退出：用真实 bwrap argv（含 ``--die-with-parent``）起一个中间父进程，
   杀掉父进程后 daemon 不能残留；
4. 超时后再次执行：资源/进程组已释放，新执行可以正常启动。

输出最后一行是 JSON；失败时也输出 JSON 并以非零退出码结束，方便宿主测试断言。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "packages" / "agent-core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from muad_agent_core.sandbox import (  # noqa: E402
    SandboxCancelled,
    SandboxPolicy,
    SandboxSpec,
    SandboxTimeout,
    build_bwrap_argv,
    build_sandbox_runner,
    sanitize_env,
)


def _inner_script(marker: str) -> str:
    """在沙箱内写 marker、拉起后台子进程和 setsid 双重 fork daemon，然后挂住等待被杀。"""
    return (
        "import os, subprocess, sys, time\n"
        "workspace = '/workspace'\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        "    pid2 = os.fork()\n"
        "    if pid2 > 0:\n"
        "        os._exit(0)\n"
        "    with open(os.path.join(workspace, 'daemon.pid'), 'w') as handle:\n"
        "        handle.write(str(os.getpid()))\n"
        "    time.sleep(120)\n"
        "    os._exit(0)\n"
        "with open(os.path.join(workspace, 'ready'), 'w') as handle:\n"
        "    handle.write(str(os.getpid()))\n"
        "print('ready', flush=True)\n"
        "time.sleep(120)\n"
    )


def _leftover_pids(marker: str) -> list[int]:
    """扫描 /proc，返回 cmdline 里仍带本次 marker 的进程（跨 PID namespace 也能看见 bwrap 子进程）。"""
    found: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    needle = marker.encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in cmdline:
            found.append(int(entry.name))
    return sorted(found)


def _wait_for_file(path: Path, *, timeout_sec: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise RuntimeError(f"marker 未出现: {path}")


def _new_spec(
    *,
    workspace: Path,
    package: Path,
    marker: str,
    timeout_sec: float,
    cancel_event: asyncio.Event | None = None,
    name: str,
) -> SandboxSpec:
    return SandboxSpec(
        policy=SandboxPolicy.for_source("USER"),
        command=(sys.executable, "-c", _inner_script(marker)),
        workspace_dir=workspace,
        package_dir=package,
        env={"SKILL_OUTPUT_DIR": "/workspace/outputs/e02", "SKILL_DATA_DIR": "/workspace/data"},
        timeout_sec=timeout_sec,
        cancel_event=cancel_event,
        name=name,
    )


async def _run_and_observe(
    runner: Any,
    spec: SandboxSpec,
    *,
    marker: str,
    workspace: Path,
    cancel_after_ready: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    task = asyncio.create_task(runner.run(spec))
    try:
        await asyncio.to_thread(_wait_for_file, workspace / "ready")
        if cancel_after_ready:
            assert spec.cancel_event is not None
            spec.cancel_event.set()
        try:
            await task
        except SandboxTimeout:
            outcome = "SANDBOX_TIMEOUT"
        except SandboxCancelled:
            outcome = "SANDBOX_CANCELLED"
        else:
            outcome = "UNEXPECTED_SUCCESS"
            raise AssertionError("沙箱执行应当被超时或取消终止")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        await asyncio.sleep(0.5)
        return {
            "status": outcome,
            "elapsed_ms": elapsed_ms,
            "orphans": _leftover_pids(marker),
            "daemon_pid": _read_pid(workspace / "daemon.pid"),
        }
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾不掩盖原始失败
                pass


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parent_death_scenario(workspace: Path, package: Path, marker: str, bwrap_path: str) -> dict[str, Any]:
    """真实 bwrap argv + 中间父进程：父进程被 SIGKILL 后 daemon 不能残留。"""
    spec = _new_spec(
        workspace=workspace,
        package=package,
        marker=marker,
        timeout_sec=120.0,
        name="e02-parent-death",
    )
    argv = build_bwrap_argv(spec, bwrap_path)
    parent_script = (
        "import json, os, subprocess, sys, time\n"
        "argv = json.loads(os.environ['BWRAP_ARGV_JSON'])\n"
        "subprocess.Popen(argv, env=json.loads(os.environ['BWRAP_ENV_JSON']))\n"
        "time.sleep(120)\n"
    )
    env = sanitize_env(spec.env, default_path=os.environ.get("PATH", os.defpath), home="/tmp")
    env["BWRAP_ARGV_JSON"] = json.dumps(argv)
    env["BWRAP_ENV_JSON"] = json.dumps({"PATH": os.environ.get("PATH", os.defpath)})
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_file(workspace / "ready")
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=10)
        time.sleep(2.0)
        return {"status": "PARENT_KILLED", "orphans": _leftover_pids(marker)}
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)


async def _async_main() -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="e02-bwrap-"))
    workspace = base / "workspace"
    package = base / "skill"
    workspace.mkdir()
    package.mkdir()
    (package / "SKILL.md").write_text("# e02\n", encoding="utf-8")

    runner = build_sandbox_runner(mode="bwrap", env="dev", platform="linux")
    bwrap_path = shutil.which("bwrap")
    assert bwrap_path is not None

    timeout_marker = "E02_TIMEOUT_MARKER_9f3c"
    timeout = await _run_and_observe(
        runner,
        _new_spec(
            workspace=workspace,
            package=package,
            marker=timeout_marker,
            timeout_sec=3.0,
            name="e02-timeout",
        ),
        marker=timeout_marker,
        workspace=workspace,
    )

    (workspace / "ready").unlink(missing_ok=True)
    (workspace / "daemon.pid").unlink(missing_ok=True)
    cancel_marker = "E02_CANCEL_MARKER_6a1d"
    cancel_event = asyncio.Event()
    cancelled = await _run_and_observe(
        runner,
        _new_spec(
            workspace=workspace,
            package=package,
            marker=cancel_marker,
            timeout_sec=120.0,
            cancel_event=cancel_event,
            name="e02-cancel",
        ),
        marker=cancel_marker,
        workspace=workspace,
        cancel_after_ready=True,
    )

    (workspace / "ready").unlink(missing_ok=True)
    (workspace / "daemon.pid").unlink(missing_ok=True)
    parent_marker = "E02_PARENT_DEATH_MARKER_b7e2"
    parent_death = await asyncio.to_thread(
        _parent_death_scenario, workspace, package, parent_marker, bwrap_path
    )

    again = SandboxSpec(
        policy=SandboxPolicy.for_source("USER"),
        command=(sys.executable, "-c", "print('ok-after-timeout')"),
        workspace_dir=workspace,
        package_dir=package,
        timeout_sec=30.0,
        name="e02-after-timeout",
    )
    result = await runner.run(again)

    return {
        "ok": (
            timeout["status"] == "SANDBOX_TIMEOUT"
            and not timeout["orphans"]
            and cancelled["status"] == "SANDBOX_CANCELLED"
            and not cancelled["orphans"]
            and parent_death["orphans"] == []
            and result.exit_code == 0
            and "ok-after-timeout" in result.stdout
        ),
        "bwrap": bwrap_path,
        "timeout": timeout,
        "cancel": cancelled,
        "parent_death": parent_death,
        "subsequent": {
            "exit_code": result.exit_code,
            "stdout": result.stdout.strip(),
        },
    }


def main() -> int:
    try:
        payload = asyncio.run(_async_main())
    except Exception as exc:  # noqa: BLE001 - 探针要把失败原因原样带回宿主测试
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
