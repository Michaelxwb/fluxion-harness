"""[S-12][RULE-snapshot-001] 无状态 Pod 替换 + 快照冻结 E2E。

真实边界：真实 Runtime Pod 子进程（uvicorn，Pod A/B）→ 真实 Console/Runtime/Worker 栈
（`task_schedule.environment.start_live_stack`）+ 真实 PostgreSQL + 真实 Artifact 根 +
真实 LLM 探针；Pod A 被真实 `SIGKILL`；断言一律从 `runtime.runtime_snapshot` /
`runtime.run_record` 真实库回读（不以 HTTP 返回值代替盘面）。

覆盖（design S-12 + RULE-snapshot-001）：
- **快照冻结**：配置变更只影响后续新 Run —— R1 的 Snapshot 行在变更前后逐列（`content_hash`
  + 四个 JSON 列 + revision/run_id）完全不变，且 `run_record.snapshot_id` 未变。
- **无 sticky session**：同一 `conversation_id` 由另一个 Pod（Pod B）接受并真实 `COMPLETED`。
- **确定性口径**：`content_hash` 以 `sha256:` 前缀；相同配置跨 Pod 两次 Run 相等、改配置后不等；
  `api_key` 不进入 `model_json`，真实 PG 轮换凭据后同配置 Run 的 hash 不变（不进 hash 输入）。
- **逐腿可归因**：两次配置变更各自只动**一条腿**（先只改 `control.agent_definition.instructions`，
  再只改 `control.model_definition.params_json`），每次都必须独立改变 `content_hash`，另一步的
  列逐列保持相等 —— 任一条腿未被冻结都会被其中一次断言抓住（不是靠「两条腿一起动」推动 hash）。
- **真实强杀**：Pod A 进程被 `SIGKILL`（`returncode == -SIGKILL`），不再存活。

如实登记的边界（不冒充覆盖）：
- 只覆盖 Runtime 侧 Run 快照（`runtime.runtime_snapshot`）；不覆盖 Console 侧控制面 revision 语义。
- Pod 是**同机 uvicorn 子进程**（独立进程 + 独立 `POD_NAME`/lease 持有者），不是容器/跨主机隔离；
  「跨 Pod」不等于跨主机。同机多进程已足以证明进程内状态不可依赖（快照只在 PG）。
- Prompt 文本本身不落库，以冻结 Snapshot 列作为持久等价证据。
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.infrastructure.models.control import AgentDefinition, ModelDefinition
from sqlalchemy import text

from tests.acceptance.im_gateway.environment import restart_process
from tests.acceptance.task_schedule.environment import (
    INTERNAL_TOKEN,
    MODEL_API_KEY,
    LiveStack,
    ServiceProcess,
    cleanup,
    clear_engine_caches,
    require,
    run_db,
    start_live_stack,
    stop_live_stack,
)

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[3]
READY_TIMEOUT_SEC = 30.0
RUN_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 0.3
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
# 配置变更（真实 PG 列写入）：让新 Run 的 resolve 结果确定性地变化。
NEW_INSTRUCTIONS = "You are the S-12 reconfigured agent."
MODEL_PARAMS = {"temperature": 0.7}
ROTATED_API_KEY = f"{MODEL_API_KEY}-rotated"
PROBE_DELAY_ENV = "OPENAI_PROBE_DELAY_MS"


# --------------------------------------------------------------------------- #
# 真实 Runtime Pod（uvicorn 子进程）
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pod_env(pod_name: str, stack: LiveStack, skill_cache_root: Path) -> dict[str, str]:
    settings = SharedSettings()
    return {
        **os.environ,
        "DATABASE_URL": settings.require_database_url(),
        "REDIS_URL": settings.require_redis_url(),
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
        "DEFAULT_TENANT_ID": stack.tenant_id,
        "CONSOLE_PLATFORM_URL": stack.console_url,
        "ARTIFACT_ROOT": str(stack.artifact_root),
        "SKILL_CACHE_ROOT": str(skill_cache_root),
        "POD_NAME": pod_name,
        # 短租约：Pod A 被强杀后由另一个 reaper 在秒级回收（RUN_ABANDONED）。
        "RUN_LEASE_SEC": "2",
        "RUN_HEARTBEAT_SEC": "1",
        "RUN_REAPER_INTERVAL_SEC": "1",
    }


class RuntimePod:
    """用例自己拉起的真实 Runtime 实例（独立进程、独立端口、独立 POD_NAME/lease）。"""

    def __init__(self, pod_name: str, stack: LiveStack, skill_cache_root: Path) -> None:
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
            env=_pod_env(pod_name, stack, skill_cache_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

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
                pass
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


# --------------------------------------------------------------------------- #
# 真实探针 / 持久盘面读写
# --------------------------------------------------------------------------- #


def _set_probe_delay(stack: LiveStack, delay_ms: int | None) -> None:
    """改真实 LLM 探针子进程的响应延迟并重启它（撑开 Run 窗口 / 还原）。"""
    process: ServiceProcess = stack.processes["llm-probe"]
    if delay_ms is None:
        process.env.pop(PROBE_DELAY_ENV, None)
    else:
        process.env[PROBE_DELAY_ENV] = str(delay_ms)
    restart_process(process)


def _read_snapshot(run_id: uuid.UUID) -> dict[str, Any]:
    """Run 的冻结 Snapshot 盘面（快照冻结的唯一持久判据）。"""

    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT s.id, s.run_id, s.schema_version, s.agent_revision, s.model_revision,"
                        " s.agent_json, s.model_json, s.skill_catalog_json, s.mcp_catalog_json,"
                        " s.policy_json, s.prompt_template_version, s.content_hash"
                        " FROM runtime.runtime_snapshot s"
                        " JOIN runtime.run_record r ON r.snapshot_id = s.id"
                        " WHERE r.id = :id"
                    ),
                    {"id": run_id},
                )
            ).one_or_none()
        if row is None:
            raise AssertionError(f"Run {run_id} 没有冻结 Snapshot 行")
        return {
            "snapshot_id": row[0],
            "run_id": row[1],
            "schema_version": row[2],
            "agent_revision": row[3],
            "model_revision": row[4],
            "agent_json": row[5],
            "model_json": row[6],
            "skill_catalog_json": row[7],
            "mcp_catalog_json": row[8],
            "policy_json": row[9],
            "prompt_template_version": row[10],
            "content_hash": row[11],
        }

    return cast("dict[str, Any]", run_db(query))


def _read_run(run_id: uuid.UUID) -> dict[str, Any]:
    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, conversation_id, snapshot_id, status, error_code"
                        " FROM runtime.run_record WHERE id = :id"
                    ),
                    {"id": run_id},
                )
            ).one()
        return {
            "id": row[0],
            "conversation_id": row[1],
            "snapshot_id": row[2],
            "status": row[3],
            "error_code": row[4],
        }

    return cast("dict[str, Any]", run_db(query))


def _wait_terminal(run_id: uuid.UUID, *, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        status = str(_read_run(run_id)["status"])
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"Run {run_id} 未在 {timeout}s 内进入终态，最后状态 {status}")


def _set_agent_instructions(stack: LiveStack) -> None:
    """真实 PG 单腿变更：**只**改 Agent instructions，Model 一行不动。"""

    async def apply(factory: Any) -> None:
        async with factory() as session:
            agent = await session.get(AgentDefinition, stack.agent_id)
            assert agent is not None, "AgentDefinition 不存在"
            assert agent.instructions != NEW_INSTRUCTIONS
            agent.instructions = NEW_INSTRUCTIONS
            await session.commit()

    run_db(apply)


def _set_model_params(stack: LiveStack) -> None:
    """真实 PG 单腿变更：**只**改 Model params_json，Agent 一行不动。"""

    async def apply(factory: Any) -> None:
        async with factory() as session:
            model = await session.get(ModelDefinition, stack.model_id)
            assert model is not None, "ModelDefinition 不存在"
            assert model.params_json != MODEL_PARAMS
            model.params_json = dict(MODEL_PARAMS)
            await session.commit()

    run_db(apply)


def _rotate_api_key(stack: LiveStack) -> None:
    """真实 PG 凭据轮换：按 B-104 口径 api_key 不进 content_hash / model_json。"""

    async def apply(factory: Any) -> None:
        async with factory() as session:
            model = await session.get(ModelDefinition, stack.model_id)
            assert model is not None, "ModelDefinition 不存在"
            assert model.api_key == MODEL_API_KEY
            model.api_key = ROTATED_API_KEY
            await session.commit()

    run_db(apply)


# --------------------------------------------------------------------------- #
# Run 驱动
# --------------------------------------------------------------------------- #


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def _headers(stack: LiveStack) -> dict[str, str]:
    return {"X-Tenant-Id": stack.tenant_id}


def _payload(stack: LiveStack, conversation_id: uuid.UUID, text_body: str) -> dict[str, Any]:
    return {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "conversation_id": str(conversation_id),
        "channel": {
            "type": "WECOM",
            "bot_id": stack.bot_id,
            "external_conversation_id": f"s12-{conversation_id}",
        },
        "message": {"id": f"msg-{uuid.uuid4().hex[:10]}", "type": "text", "text": text_body},
    }


def _create_conversation(pod: RuntimePod, stack: LiveStack) -> uuid.UUID:
    response = httpx.post(
        f"{pod.url}/v1/conversations",
        json={"agent_id": str(stack.agent_id), "platform_user_id": str(stack.platform_user_id)},
        headers=_headers(stack),
        timeout=10,
    )
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["conversation_id"])


def _start_run_streaming(
    pod: RuntimePod, stack: LiveStack, conversation_id: uuid.UUID, text_body: str
) -> uuid.UUID:
    """发起 Run 并在 `run.created` 后立即返回（Run 继续在后台执行，便于中途强杀）。"""
    with httpx.stream(
        "POST",
        f"{pod.url}/v1/runs",
        json=_payload(stack, conversation_id, text_body),
        headers=_headers(stack),
        timeout=RUN_TIMEOUT_SEC,
    ) as stream:
        for line in stream.iter_lines():
            if line.startswith("data:"):
                event = json.loads(line[len("data:") :].strip())
                assert event["type"] == "run.created", event
                return uuid.UUID(str(event["run_id"]))
    raise AssertionError("Run 未产出 run.created 事件")


def _start_run(
    pod: RuntimePod, stack: LiveStack, conversation_id: uuid.UUID, text_body: str
) -> tuple[uuid.UUID, uuid.UUID]:
    response = httpx.post(
        f"{pod.url}/v1/runs",
        json=_payload(stack, conversation_id, text_body),
        headers=_headers(stack),
        timeout=RUN_TIMEOUT_SEC,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert events and events[0]["type"] == "run.created", events[:1]
    assert events[-1]["type"] == "run.completed", events[-3:]
    assert events[-1]["data"]["status"] == "COMPLETED"
    return (
        uuid.UUID(str(events[0]["run_id"])),
        uuid.UUID(str(events[0]["data"]["conversation_id"])),
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    """真实 Console/Runtime/Worker 子进程栈 + 真实 PG/Redis（复用 08/09 验收栈原语）。"""
    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("dfx-stateless")
    stack, processes = start_live_stack(root)
    try:
        yield stack
    finally:
        stop_live_stack(processes)
        cleanup(database_url, stack.artifact_root)
        clear_engine_caches()


# --------------------------------------------------------------------------- #
# S-12：无状态 Pod 替换与快照冻结
# --------------------------------------------------------------------------- #


def test_s12_stateless_pod_replacement_freezes_snapshot_and_deterministic_hash(
    live_stack: LiveStack,
) -> None:
    """[S-12][RULE-snapshot-001] Pod 被强杀后由另一 Pod 续跑；旧 Snapshot 行逐列冻结。"""
    stack = live_stack
    original_delay = stack.processes["llm-probe"].env.get(PROBE_DELAY_ENV)
    skill_cache_root = stack.artifact_root.parent / "pod-skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)

    pod_a: RuntimePod | None = None
    pod_b: RuntimePod | None = None
    try:
        pod_a = RuntimePod("s12-pod-a", stack, skill_cache_root)
        pod_b = RuntimePod("s12-pod-b", stack, skill_cache_root)
        assert pod_b.url != pod_a.url, "Pod A/B 必须是两个独立实例"

        conversation = _create_conversation(pod_a, stack)

        # 1) R1 在 Pod A 上跑到中途（探针延迟 5s 撑开强杀窗口），回读其 Snapshot 盘面
        _set_probe_delay(stack, 5000)
        run1 = _start_run_streaming(pod_a, stack, conversation, "s12 turn one")
        snapshot1 = _read_snapshot(run1)
        run1_before = _read_run(run1)
        assert snapshot1["run_id"] == run1
        assert snapshot1["content_hash"].startswith("sha256:")
        assert "api_key" not in snapshot1["model_json"]
        assert MODEL_API_KEY not in json.dumps(snapshot1["model_json"])
        assert run1_before["status"] in {"CREATED", "RUNNING"}, run1_before
        assert run1_before["conversation_id"] == conversation

        # 2) 真实 SIGKILL 掉 Pod A（进程确实退出）
        pod_a.kill()
        assert not pod_a.alive, "Pod A 仍存活"
        assert pod_a.returncode == -signal.SIGKILL, pod_a.returncode

        # 3) 相同配置、跨 Pod：content_hash 相等（确定性口径）
        _set_probe_delay(stack, 0)
        baseline_conversation = _create_conversation(pod_b, stack)
        run0, _ = _start_run(pod_b, stack, baseline_conversation, "s12 baseline")
        snapshot0 = _read_snapshot(run0)
        assert snapshot0["content_hash"] == snapshot1["content_hash"], (
            snapshot0["content_hash"],
            snapshot1["content_hash"],
        )
        assert snapshot0["run_id"] != snapshot1["run_id"]
        assert snapshot0["agent_json"] == snapshot1["agent_json"]

        # 4) 凭据轮换不进 content_hash / model_json：同配置 Run 的 hash 仍相等
        _rotate_api_key(stack)
        rotated_conversation = _create_conversation(pod_b, stack)
        run0b, _ = _start_run(pod_b, stack, rotated_conversation, "s12 rotated key")
        baseline = _read_snapshot(run0b)
        assert baseline["content_hash"] == snapshot0["content_hash"]
        assert "api_key" not in baseline["model_json"]
        assert ROTATED_API_KEY not in json.dumps(baseline["model_json"])

        # 5) 单腿变更 A：只改 Agent instructions（Model 一行不动）⇒ hash 必须变
        _set_agent_instructions(stack)
        agent_conversation = _create_conversation(pod_b, stack)
        run_a, _ = _start_run(pod_b, stack, agent_conversation, "s12 agent leg")
        snapshot_a = _read_snapshot(run_a)
        assert snapshot_a["agent_json"]["instructions"] == NEW_INSTRUCTIONS
        assert snapshot_a["agent_json"] != baseline["agent_json"]
        # revision 列未被原地更新触碰：hash 变化来自内容本身，不是版本号
        assert snapshot_a["agent_revision"] == baseline["agent_revision"]
        assert snapshot_a["model_json"] == baseline["model_json"], "Agent 腿不得改动 model 列"
        assert snapshot_a["content_hash"] != baseline["content_hash"], (
            "只改 Agent instructions 也必须改变 content_hash（agent 腿未被冻结）"
        )

        # 6) 单腿变更 B：只改 Model params（Agent 一行不动）⇒ 相对 A 的 hash 必须再变
        _set_model_params(stack)
        model_conversation = _create_conversation(pod_b, stack)
        run_b, _ = _start_run(pod_b, stack, model_conversation, "s12 model leg")
        snapshot_b = _read_snapshot(run_b)
        assert snapshot_b["model_json"]["params"] == MODEL_PARAMS
        assert snapshot_b["model_json"] != snapshot_a["model_json"]
        assert snapshot_b["agent_json"] == snapshot_a["agent_json"], "Model 腿不得改动 agent 列"
        assert snapshot_b["content_hash"] != snapshot_a["content_hash"], (
            "只改 Model params 也必须改变 content_hash（model 腿未被冻结）"
        )

        # R1 被强杀后由 reaper 回收为 RUN_ABANDONED（同一 conversation 才能再发起 Run）
        terminal1 = _wait_terminal(run1, timeout=30)
        assert terminal1 == "FAILED", terminal1
        assert _read_run(run1)["error_code"] == "RUN_ABANDONED"

        # 7) Turn 2：另一 Pod、同一 conversation_id（无 sticky session 的直接证据）
        run2, conversation2 = _start_run(pod_b, stack, conversation, "s12 turn two")
        assert conversation2 == conversation
        assert run2 != run1

        snapshot2 = _read_snapshot(run2)
        assert snapshot2["agent_json"]["instructions"] == NEW_INSTRUCTIONS
        assert snapshot2["model_json"]["params"] == MODEL_PARAMS
        assert snapshot2["content_hash"] != snapshot1["content_hash"]
        # 两腿都生效后与最近一次同配置 Run 的 hash 相等（跨 conversation 亦确定）
        assert snapshot2["content_hash"] == snapshot_b["content_hash"]
        assert snapshot2["content_hash"].startswith("sha256:")
        assert "api_key" not in snapshot2["model_json"]
        assert MODEL_API_KEY not in json.dumps(snapshot2["model_json"])

        # 8) R1 的快照行完全不漂移：content_hash + 四个 JSON 列逐列相等，run_id/snapshot_id 未变
        snapshot1_after = _read_snapshot(run1)
        run1_after = _read_run(run1)
        assert snapshot1_after == snapshot1
        assert snapshot1_after["agent_json"] == snapshot1["agent_json"]
        assert snapshot1_after["model_json"] == snapshot1["model_json"]
        assert snapshot1_after["skill_catalog_json"] == snapshot1["skill_catalog_json"]
        assert snapshot1_after["mcp_catalog_json"] == snapshot1["mcp_catalog_json"]
        assert run1_after["snapshot_id"] == run1_before["snapshot_id"]
        assert snapshot1_after["run_id"] == run1
    finally:
        _set_probe_delay(stack, int(original_delay) if original_delay is not None else None)
        if pod_b is not None:
            pod_b.stop()
        if pod_a is not None:
            pod_a.kill()
