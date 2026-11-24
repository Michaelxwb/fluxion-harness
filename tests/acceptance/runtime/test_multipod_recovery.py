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
import pytest
import sqlalchemy as sa
from muad_common import SharedSettings

from tests.acceptance.runtime.conftest import LiveServices, run_db

ENTRY_ID = uuid.uuid4()

ROOT = Path(__file__).resolve().parents[3]
READY_TIMEOUT_SEC = 20.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime_env(pod_name: str, stack: LiveServices) -> dict[str, str]:
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
        "RUN_LEASE_SEC": "3",
        "RUN_HEARTBEAT_SEC": "1",
        "RUN_REAPER_INTERVAL_SEC": "1",
    }


class RuntimePod:
    def __init__(self, pod_name: str, stack: LiveServices) -> None:
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


def _headers(stack: LiveServices) -> dict[str, str]:
    return stack.runtime_headers()


def _payload(stack: LiveServices, text: str, conversation_id: uuid.UUID) -> dict[str, Any]:
    return {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "conversation_id": str(conversation_id),
        "channel": {
            "type": "WECOM",
            "entry_id": str(ENTRY_ID),
            "bot_id": "bot-acc",
            "external_conversation_id": "ext-acc",
        },
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


def _wait_running_with_lease(run_id: str, pod_name: str, timeout_sec: float = 20.0) -> None:
    """只有即将被强杀的 Pod 持有租约，才能验证回收路径。"""

    async def read(factory: Any) -> tuple[str, str | None]:
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT status, lease_owner FROM runtime.run_record WHERE id = :id"
                    ),
                    {"id": uuid.UUID(run_id)},
                )
            ).one()
        return str(row[0]), row[1]

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        status, owner = run_db(read)
        if status == "RUNNING" and owner:
            assert owner.startswith(f"{pod_name}:"), (run_id, owner)
            return
        time.sleep(0.1)
    raise AssertionError(f"Run 未在 {timeout_sec}s 内进入 RUNNING+租约：{run_id}")


def _create_conversation(pod: RuntimePod, services: LiveServices) -> uuid.UUID:
    response = httpx.post(
        f"{pod.url}/v1/conversations",
        json={
            "agent_id": str(services.agent_id),
            "platform_user_id": str(services.platform_user_id),
            "channel": {"type": "WECOM", "entry_id": str(ENTRY_ID), "bot_id": "bot-acc"},
        },
        headers=_headers(services),
        timeout=10,
    )
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["conversation_id"])


def _start_run(pod: RuntimePod, services: LiveServices, conversation_id: uuid.UUID) -> str:
    with httpx.stream(
        "POST",
        f"{pod.url}/v1/runs",
        json=_payload(services, "crash me", conversation_id),
        headers=_headers(services),
        timeout=30,
    ) as stream:
        assert stream.status_code == 200
        for line in stream.iter_lines():
            if line.startswith("data:"):
                event = json.loads(line[len("data:") :].strip())
                assert event["type"] == "run.created"
                return str(event["run_id"])
    pytest.fail("Runtime 未发送 run.created")


def _assert_reaped(pod: RuntimePod, services: LiveServices, run_id: str) -> None:
    # CI 整跑时，Reaper 在租约过期后可能滞后一段时间才执行。
    deadline = time.monotonic() + 120
    data: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{pod.url}/v1/runs/{run_id}", headers=_headers(services), timeout=10
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        if data["status"] == "FAILED":
            break
        assert data["status"] == "RUNNING", data
        time.sleep(0.5)
    assert data["status"] == "FAILED", data
    assert data["error_code"] == "RUN_ABANDONED", data
    assert data["end_time"] is not None


def _assert_takeover(
    pod: RuntimePod, services: LiveServices, conversation_id: uuid.UUID, abandoned_run_id: str
) -> None:
    takeover = httpx.post(
        f"{pod.url}/v1/runs",
        json=_payload(services, "takeover", conversation_id),
        headers=_headers(services),
        timeout=30,
    )
    assert takeover.status_code == 200, takeover.text
    events = [
        json.loads(line[len("data:") :].strip())
        for line in takeover.text.splitlines()
        if line.startswith("data:")
    ]
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"
    assert events[0]["run_id"] != abandoned_run_id


def test_s04_e07_process_kill_reaped_and_takeover(live_services: LiveServices) -> None:
    """[S-04][E-07] Pod A 被强杀 → Reaper 回收 RUN_ABANDONED → Pod B 接管同一会话。"""
    script_url = f"{live_services.llm_url}/script"
    pod_a = RuntimePod("pod-a", live_services)
    try:
        # 模型探针在测试进程内，显式设置探针行为并留出强杀窗口。
        httpx.post(script_url, json={"delay_ms": 60_000}, timeout=10).raise_for_status()
        conversation_id = _create_conversation(pod_a, live_services)
        run_id = _start_run(pod_a, live_services, conversation_id)
        _wait_running_with_lease(run_id, pod_a.pod_name)

        # A 领取之后才启动 B，避免 B 抢到本次 Run。
        pod_b = RuntimePod("pod-b", live_services)
        try:
            pod_a.kill()
            _assert_reaped(pod_b, live_services, run_id)
            httpx.post(script_url, json={}, timeout=10).raise_for_status()
            _assert_takeover(pod_b, live_services, conversation_id, run_id)
        finally:
            pod_b.stop()
    finally:
        pod_a.kill()
        httpx.post(script_url, json={}, timeout=10).raise_for_status()
