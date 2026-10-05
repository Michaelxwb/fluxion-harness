"""E-09 模型恢复验收（integration）：对准**生产恢复链**，不是未接线的 `ModelGateway`。

真实边界：**真实 Runtime 服务（真实 HTTP）→ 真实 PostgreSQL**，provider 侧由**本模块自造的真实
HTTP 探针**承载（Runtime 经真实 HTTP 调用该端点；非拦截、非响应改写、无 monkeypatch）。
探针按用例注入 429+`Retry-After`（**响应头**）、5xx、连接重置、以及"一直限流"等形态。

覆盖（E-09，逐条对齐 design §3.4.6 的"代码现状"列）：
- 429 + `Retry-After`：按其等待后重试（`_retry_delay` 优先用响应头值）；
- 5xx / 连接重置：指数退避重试（`RETRY_BASE_SEC * 2**attempt`）；
- deadline 不足：**不重试**，Run 进失败终态（落库错误码 `COMMON_INTERNAL_ERROR`）；
- cancel：每轮与退避 sleep 分片都检查取消 ⇒ **立即停止**，provider 调用数不再增长；
- 审计：`runtime.model_invocation_audit` 逐 attempt 一行（`status`/`retry_reason`/`error_code`/`run_id`），
  且**不含密钥明文**（该表无任何 payload 列，`api_key` 只进 `Authorization` 头）。

**显式边界（不修，只登记；本任务不覆盖，另立整改）**：
1. 退避**无 jitter** —— 生产为纯指数（`runner.py:477`），故本模块只断言指数形状，不断言 jitter；
2. `prompt too long` 的"一次 Context rebuild/compaction 后重试"**整条能力不存在**（全仓无
   `compaction`/`rebuild`/`context_length` 机制）⇒ 无可断言对象；
3. `ModelGateway`（`apps/agent-runtime/.../application/model_gateway.py`）**未接入生产**，
   仅 `tests/agent_runtime/test_model_recovery.py` 驱动它 ⇒ 本模块不拿它当验收对象。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from tests.acceptance.runtime.conftest import LiveStack
from tests.acceptance.runtime.conftest import (
    live_stack as _runtime_live_stack,  # noqa: F401  (跨 conftest 复用夹具，本模块同名夹具优先)
)
from tests.acceptance.task_schedule.environment import run_db

pytestmark = pytest.mark.integration

@pytest.fixture(scope="module")
def live_stack(_runtime_live_stack: LiveStack) -> LiveStack:  # noqa: F811  (参数名即夹具名)
    """复用 runtime 验收栈（真实 Console/Runtime 服务 + 真实 PG/探针）。

    本模块只把种子模型的 `base_url` 指向自带的**故障探针**（真实 HTTP 端点），
    其余（Runtime/Console/PG/审计）全部复用既有真实栈。
    """
    return _runtime_live_stack

MODEL_API_KEY = "recovery-probe-key-9f2c"
RUN_TIMEOUT_SEC = 60.0
CANCEL_TIMEOUT_SEC = 20.0


# --------------------------------------------------------------------------- #
# 可注入故障的真实 HTTP 探针（Runtime 真调用它）
# --------------------------------------------------------------------------- #


class FaultProbe:
    """按脚本依次给出响应的真实 provider 端点；并记录每次调用的时刻。"""

    def __init__(self) -> None:
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.plan: list[str] = []      # 队列：'ok' | '429:<seconds>' | '500' | 'reset' | 'hang'
        self.calls: list[float] = []   # 每次请求到达的 monotonic 时刻
        self.auth_headers: list[str] = []
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

        @self.app.get("/healthz")
        async def healthz() -> dict[str, str]:  # pragma: no cover - 探针自检
            return {"status": "ok"}

        @self.app.post("/v1/chat/completions")
        async def completions(request: Request) -> Response:  # noqa: ANN202
            self.calls.append(time.monotonic())
            self.auth_headers.append(request.headers.get("authorization", ""))
            step = self.plan.pop(0) if self.plan else "ok"
            if step == "reset":
                raise ConnectionResetError("probe: injected connection reset")
            if step.startswith("429"):
                seconds = step.split(":", 1)[1]
                return Response(
                    content='{"error":{"message":"rate limited","type":"rate_limit_error"}}',
                    status_code=429,
                    headers={"Retry-After": seconds, "Content-Type": "application/json"},
                )
            if step == "500":
                return Response(
                    content='{"error":{"message":"probe failure","type":"server_error"}}',
                    status_code=500,
                    media_type="application/json",
                )
            if step.startswith("hang"):
                await asyncio.sleep(float(step.split(":", 1)[1]))
            return Response(
                content=(
                    '{"id":"cmpl-probe","object":"chat.completion","model":"gpt-4o-mini",'
                    '"choices":[{"index":0,"finish_reason":"stop","message":'
                    '{"role":"assistant","content":"probe-ok"}}],'
                    '"usage":{"prompt_tokens":5,"completion_tokens":3}}'
                ),
                media_type="application/json",
            )

    def start(self) -> str:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            servers = getattr(self._server, "servers", None)
            if servers:
                port = servers[0].sockets[0].getsockname()[1]
                self.base_url = f"http://127.0.0.1:{port}"
                break
            time.sleep(0.02)
        assert self.base_url, "故障探针未能启动"
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    def gaps(self) -> list[float]:
        """相邻两次调用的时间间隔（秒）——退避/`Retry-After` 的观测口径。"""
        return [b - a for a, b in zip(self.calls, self.calls[1:], strict=False)]


@pytest.fixture(scope="module")
def fault_probe() -> Iterator[FaultProbe]:
    probe = FaultProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


# --------------------------------------------------------------------------- #
# 真实 PG：把种子模型指向本模块探针 + 可调 agent 策略；审计回读
# --------------------------------------------------------------------------- #


def _execute(statement: str, params: dict[str, Any]) -> None:
    """在**子线程**里跑独立事件循环访问真实 PG（`run_db`）：绝不在主线程用 `asyncio.run`,
    否则会把主线程的事件循环置空，导致本会话后续所有异步套件崩（"no current event loop"）。"""

    async def run(factory: Any) -> None:
        async with factory.begin() as connection:
            await connection.execute(text(statement), params)

    run_db(run)


def _select(statement: str, params: dict[str, Any]) -> list[Any]:
    async def run(factory: Any) -> list[Any]:
        async with factory() as connection:
            result = await connection.execute(text(statement), params)
            return list(result.all())

    return list(run_db(run))


def _point_model_at_probe(stack: LiveStack, probe: FaultProbe) -> None:
    _execute(
        "UPDATE control.model_definition SET base_url = :u, api_key = :k WHERE id = :id",
        {"u": f"{probe.base_url}/v1", "k": MODEL_API_KEY, "id": stack.model_id},
    )


def _set_policy(stack: LiveStack, **policy: Any) -> None:
    import json

    _execute(
        "UPDATE control.agent_definition SET runtime_config_json = cast(:c as jsonb) WHERE id = :id",
        {"c": json.dumps(policy), "id": stack.agent_id},
    )


def _audit_rows(stack: LiveStack, run_id: str) -> list[dict[str, Any]]:
    rows = _select(
        "SELECT attempt, status, retry_reason, error_code, provider, model, "
        "       input_tokens, output_tokens, latency_ms "
        "FROM runtime.model_invocation_audit WHERE tenant_id = :t AND run_id = :r "
        "ORDER BY attempt",
        {"t": stack.tenant_id, "r": uuid.UUID(run_id)},
    )
    keys = ("attempt", "status", "retry_reason", "error_code", "provider", "model",
            "input_tokens", "output_tokens", "latency_ms")
    return [dict(zip(keys, row, strict=True)) for row in rows]


# --------------------------------------------------------------------------- #
# 驱动：真实 runtime 起 Run（SSE），返回终态事件
# --------------------------------------------------------------------------- #


def _payload(stack: LiveStack, text: str) -> dict[str, Any]:
    return {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "channel": {
            "type": "WECOM",
            "bot_id": "bot-acc",
            "external_conversation_id": f"c-{uuid.uuid4().hex[:8]}",
        },
        "message": {"id": f"m-{uuid.uuid4().hex[:8]}", "type": "text", "text": text},
    }


def _run(
    stack: LiveStack,
    probe: FaultProbe,
    *,
    text: str = "hello",
    wait: float = RUN_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """发起一轮真实 Run 并读完 SSE（终态事件在末尾）。"""
    events: list[dict[str, Any]] = []
    with httpx.Client(timeout=wait) as client:
        with client.stream(
            "POST", f"{stack.runtime_url}/v1/runs", json=_payload(stack, text),
            headers={"X-Tenant-Id": stack.tenant_id},
        ) as response:
            assert response.status_code == 200, response.read()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    import json

                    events.append(json.loads(line[len("data:"):].strip()))
    del probe
    return events


def _run_row(stack: LiveStack, run_id: str) -> dict[str, Any]:
    rows = _select(
        "SELECT status, error_code, error_message, cancel_requested FROM runtime.run_record "
        "WHERE tenant_id = :t AND id = :r",
        {"t": stack.tenant_id, "r": uuid.UUID(run_id)},
    )
    assert rows, "run 行不存在"
    return dict(zip(("status", "error_code", "error_message", "cancel_requested"), rows[0], strict=True))


# --------------------------------------------------------------------------- #
# E-09 用例
# --------------------------------------------------------------------------- #


def test_e09_rate_limit_retry_after_header_is_honoured(
    live_stack: LiveStack, fault_probe: FaultProbe
) -> None:
    """429 + `Retry-After`：按其等待后重试（响应头值优先于指数退避）。"""
    _point_model_at_probe(live_stack, fault_probe)
    _set_policy(live_stack, max_model_retries=3, deadline_ms=120_000)
    fault_probe.plan = ["429:1", "ok"]
    fault_probe.calls.clear()

    events = _run(live_stack, fault_probe)
    run_id = str(events[0]["run_id"])
    assert events[-1]["type"] == "run.completed", events[-1]
    assert run_id
    assert len(fault_probe.calls) == 2, f"应恰好重试一次：{len(fault_probe.calls)}"
    gap = fault_probe.gaps()[0]
    assert 0.9 <= gap <= 1.6, f"退避未按 Retry-After=1s：实测 {gap:.2f}s"

    rows = _audit_rows(live_stack, run_id)
    assert [row["status"] for row in rows] == ["RETRY", "OK"], rows
    assert rows[0]["retry_reason"] == "RATE_LIMITED", rows[0]


def test_e09_unavailable_backs_off_exponentially(
    live_stack: LiveStack, fault_probe: FaultProbe
) -> None:
    """5xx：指数退避重试（base * 2**attempt），且不越界为 jitter 之类未实现行为。"""
    _point_model_at_probe(live_stack, fault_probe)
    _set_policy(live_stack, max_model_retries=3, deadline_ms=120_000)
    fault_probe.plan = ["500", "500", "ok"]
    fault_probe.calls.clear()

    events = _run(live_stack, fault_probe)
    run_id = str(events[0]["run_id"])
    assert events[-1]["type"] == "run.completed", events[-1]
    gaps = fault_probe.gaps()
    assert len(gaps) == 2, f"应恰好重试两次：{gaps}"
    # 指数：第 1 次≈base(0.1s)，第 2 次≈2*base(0.2s)；留足调度余量
    assert 0.08 <= gaps[0] <= 0.45, gaps
    assert gaps[1] >= gaps[0] * 1.4, f"退避未呈指数增长：{gaps}"

    rows = _audit_rows(live_stack, run_id)
    assert [row["status"] for row in rows] == ["RETRY", "RETRY", "OK"], rows
    assert {row["retry_reason"] for row in rows[:2]} == {"UNAVAILABLE"}, rows


def test_e09_connection_reset_is_retried(live_stack: LiveStack, fault_probe: FaultProbe) -> None:
    """连接重置（transport error）归 `ModelUnavailableError` ⇒ 同样重试。"""
    _point_model_at_probe(live_stack, fault_probe)
    _set_policy(live_stack, max_model_retries=3, deadline_ms=120_000)
    fault_probe.plan = ["reset", "ok"]
    fault_probe.calls.clear()

    events = _run(live_stack, fault_probe)
    assert events[-1]["type"] == "run.completed", events[-1]
    assert len(fault_probe.calls) == 2, fault_probe.calls


def test_e09_deadline_exhausted_fails_without_retrying(
    live_stack: LiveStack, fault_probe: FaultProbe
) -> None:
    """deadline 不足：不再重试，Run 失败终态（`_retry_delay` 判定后抛 `RunnerDeadlineExceeded`）。"""
    _point_model_at_probe(live_stack, fault_probe)
    # Retry-After 远大于剩余 deadline ⇒ 不重试
    _set_policy(live_stack, max_model_retries=3, deadline_ms=1_000)
    fault_probe.plan = ["429:30"] * 5
    fault_probe.calls.clear()

    events = _run(live_stack, fault_probe, wait=RUN_TIMEOUT_SEC)
    run_id = str(events[0]["run_id"])
    assert events[-1]["type"] == "run.failed", events[-1]
    assert len(fault_probe.calls) == 1, f"deadline 不足时不得重试：{len(fault_probe.calls)} 次"

    row = _run_row(live_stack, run_id)
    assert row["status"] == "FAILED", row
    # 生产口径：RunnerDeadlineExceeded 经 _error_code_for 落 COMMON_INTERNAL_ERROR
    assert row["error_code"] == "COMMON_INTERNAL_ERROR", row


def test_e09_cancel_stops_retries_immediately(live_stack: LiveStack, fault_probe: FaultProbe) -> None:
    """等待中收到 cancel：退避 sleep 分片检查取消 ⇒ 立即停止，provider 调用数不再增长。"""
    _point_model_at_probe(live_stack, fault_probe)
    _set_policy(live_stack, max_model_retries=5, deadline_ms=120_000)
    fault_probe.plan = ["429:30"] * 10   # 首次调用后就进入长退避
    fault_probe.calls.clear()

    with httpx.Client(timeout=RUN_TIMEOUT_SEC) as client:
        streamed: list[dict[str, Any]] = []
        with client.stream(
            "POST", f"{live_stack.runtime_url}/v1/runs",
            json=_payload(live_stack, "cancel me"),
            headers={"X-Tenant-Id": live_stack.tenant_id},
        ) as response:
            assert response.status_code == 200, response.read()
            import json

            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):].strip())
                streamed.append(event)
                if len(streamed) == 1:
                    assert event["type"] == "run.created", event
                    # 必须在流内发取消并**继续读完**：提前退出会关掉 SSE，执行器随之停摆
                    cancelled_at = time.monotonic()
                    cancelled = client.post(
                        f"{live_stack.runtime_url}/v1/runs/cancel-active",
                        json={
                            "agent_id": str(live_stack.agent_id),
                            "platform_user_id": str(live_stack.platform_user_id),
                        },
                        headers={"X-Tenant-Id": live_stack.tenant_id},
                    )
                    assert cancelled.status_code == 200, cancelled.text
                    assert cancelled.json()["data"]["status"] == "CANCELLING", cancelled.text

    cancel_to_terminal_sec = time.monotonic() - cancelled_at
    assert streamed[-1]["type"] == "run.completed", streamed[-1]
    assert streamed[-1]["data"]["status"] == "CANCELLED", streamed[-1]
    # **时间有界**：正确实现下退避 sleep 每 0.1s 检查取消，取消到终态应远小于当前 Retry-After(30s)；
    # 若取消感知失效（退避变成一次性 sleep），这里会等到 30s 才终止 ⇒ 该上界即判据。
    assert cancel_to_terminal_sec <= 5.0, f"取消未立即生效：{cancel_to_terminal_sec:.1f}s（Retry-After=30s）"

    row = _run_row(live_stack, str(streamed[0]["run_id"]))
    assert row["status"] == "CANCELLED", row
    calls_at_cancel = len(fault_probe.calls)
    time.sleep(1.0)   # 若仍在按 Retry-After=30 退避，这一秒内不会有新调用；有则说明未停止
    assert len(fault_probe.calls) == calls_at_cancel, "取消后重试未立即停止"


def test_e09_audit_rows_carry_attempts_and_no_secret_plaintext(
    live_stack: LiveStack, fault_probe: FaultProbe
) -> None:
    """审计逐 attempt 一行且**不含密钥明文**（该表无 payload 列，api_key 只进 Authorization 头）。"""
    _point_model_at_probe(live_stack, fault_probe)
    _set_policy(live_stack, max_model_retries=3, deadline_ms=120_000)
    fault_probe.plan = ["429:1", "500", "ok"]
    fault_probe.calls.clear()

    events = _run(live_stack, fault_probe)
    run_id = str(events[0]["run_id"])
    rows = _audit_rows(live_stack, run_id)
    # 生产 writer（`AuditedModelProvider._invoke`）的 attempt 计数器**从 0 起算**
    assert [row["attempt"] for row in rows] == [0, 1, 2], rows
    assert [row["status"] for row in rows] == ["RETRY", "RETRY", "OK"], rows
    assert rows[0]["retry_reason"] == "RATE_LIMITED" and rows[1]["retry_reason"] == "UNAVAILABLE", rows
    serialized = str(rows)
    assert MODEL_API_KEY not in serialized, "审计行不得出现密钥明文"
    assert "api_key" not in serialized
    # 探针侧确实收到了带 Bearer 的调用（证明密钥只走请求头，不进审计）
    assert any(MODEL_API_KEY in header for header in fault_probe.auth_headers), fault_probe.auth_headers[:2]
