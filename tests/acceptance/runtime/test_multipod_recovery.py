"""[S-04][E-07][RULE-arch-001] 跨 Pod 重建与断流崩溃恢复 E2E。

真实边界：独立 Runtime 进程（uvicorn 子进程）→ PostgreSQL lease → Reaper → GET Run；
Pod 进程被 SIGKILL 后由另一实例回收，conversation 无 sticky session 可继续新 Run。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from muad_common import SharedSettings

from tests.acceptance.runtime.conftest import LiveStack

ROOT = Path(__file__).resolve().parents[3]
READY_TIMEOUT_SEC = 20.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime_env(pod_name: str, stack: LiveStack) -> dict[str, str]:
    settings = SharedSettings()
    return {
        **os.environ,
        "DATABASE_URL": settings.require_database_url(),
        "REDIS_URL": settings.require_redis_url(),
        "INTERNAL_SERVICE_TOKEN": os.environ["INTERNAL_SERVICE_TOKEN"],
        "CONSOLE_PLATFORM_URL": stack.console_url,
        "ARTIFACT_ROOT": str(stack.artifact_root),
        "SKILL_CACHE_ROOT": os.environ["SKILL_CACHE_ROOT"],
        "POD_NAME": pod_name,
        "RUN_LEASE_SEC": "2",
        "RUN_HEARTBEAT_SEC": "1",
        "RUN_REAPER_INTERVAL_SEC": "1",
    }


class RuntimePod:
    def __init__(self, pod_name: str, stack: LiveStack) -> None:
        self.pod_name = pod_name
        self._port = _free_port()
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "muad_agent_runtime.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._port),
                "--log-level",
                "error",
            ],
            cwd=ROOT,
            env=_runtime_env(pod_name, stack),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"{self.pod_name} exited early: {self._process.returncode}")
            try:
                response = httpx.get(f"{self.url}/healthz", timeout=1)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(f"{self.pod_name} did not become ready")

    def kill(self) -> None:
        if self._process.poll() is None:
            self._process.send_signal(signal.SIGKILL)
            self._process.wait(timeout=10)

    def stop(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.kill()


def _headers(stack: LiveStack) -> dict[str, str]:
    return stack.runtime_headers()


def _payload(stack: LiveStack, text: str, conversation_id: uuid.UUID) -> dict[str, Any]:
    return {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "conversation_id": str(conversation_id),
        "channel": {"type": "WECOM", "bot_id": "bot-acc", "external_conversation_id": "ext-acc"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


def test_s04_e07_process_kill_reaped_and_takeover(live_stack: LiveStack) -> None:
    """[S-04][E-07] Pod A 被强杀 → Reaper 回收 RUN_ABANDONED → Pod B 接管同一 conversation。"""
    pod_a = RuntimePod("pod-a", live_stack)
    pod_b = RuntimePod("pod-b", live_stack)
    try:
        conversation = httpx.post(
            f"{pod_a.url}/v1/conversations",
            json={
                "agent_id": str(live_stack.agent_id),
                "platform_user_id": str(live_stack.platform_user_id),
            },
            headers=_headers(live_stack),
            timeout=10,
        )
        assert conversation.status_code == 200, conversation.text
        conversation_id = uuid.UUID(conversation.json()["data"]["conversation_id"])

        os.environ["OPENAI_PROBE_DELAY_MS"] = "5000"
        run_id: str | None = None
        with httpx.stream(
            "POST",
            f"{pod_a.url}/v1/runs",
            json=_payload(live_stack, "crash me", conversation_id),
            headers=_headers(live_stack),
            timeout=30,
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[len("data:") :].strip())
                    assert event["type"] == "run.created"
                    run_id = event["run_id"]
                    break
        assert run_id is not None
        pod_a.kill()

        # 另一实例的 Reaper 在 lease 过期后回收（RUN_ABANDONED 终态，GET Run 可见）
        deadline = time.monotonic() + 15
        data: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = httpx.get(
                f"{live_stack.runtime_url}/v1/runs/{run_id}",
                headers=_headers(live_stack),
                timeout=10,
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            if data["status"] == "FAILED":
                break
            time.sleep(0.5)
        assert data["status"] == "FAILED"
        assert data["error_code"] == "RUN_ABANDONED"
        assert data["end_time"] is not None

        # S-04：Pod B 在同一 conversation 创建新 Run（无 sticky session），并真实执行完成
        os.environ["OPENAI_PROBE_DELAY_MS"] = "0"
        takeover = httpx.post(
            f"{pod_b.url}/v1/runs",
            json=_payload(live_stack, "takeover", conversation_id),
            headers=_headers(live_stack),
            timeout=30,
        )
        assert takeover.status_code == 200, takeover.text
        events = []
        for block in takeover.text.split("\n\n"):
            data_lines = [
                line[len("data:") :].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if data_lines:
                events.append(json.loads("\n".join(data_lines)))
        assert events[-1]["type"] == "run.completed"
        assert events[-1]["data"]["status"] == "COMPLETED"
        assert events[0]["run_id"] != run_id
    finally:
        os.environ.pop("OPENAI_PROBE_DELAY_MS", None)
        pod_b.stop()
        pod_a.kill()
