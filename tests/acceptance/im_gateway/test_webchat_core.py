"""[S-08/TASK-010] WebChat 走统一消息核心的 SYNC 受理，不重复 Console 解析。

不得 Mock 的真实边界：

- **真实 Gateway / Console / Runtime / Worker 子进程**（`gateway_stack`：真实 uvicorn 进程、
  真实 PostgreSQL/Redis、真实模型探针）。
- **真实 CHAT 身份**：Console 真实 Token 交换（PG `control.chat_access_token` →
  `control.chat_session`）产出 Cookie + 上下文，并把会话写入 Redis；网关从 Redis 读取主体。
- **Console resolve 计数**取自 Console 进程真实 `GET /metrics`
  （`console_api_requests_total{path="/internal/channel/chat-principal"}`）：Redis 命中后
  提交路径不得回源 Console（计数增量为 0）。
- **202 延迟**是真实 HTTP 往返采样（与受理 SLO 同口径 P95 ≤ 500ms）。

断言要点：

- 同一 message_id + 同一 Idempotency-Key 重试只建一个 Run（`runtime.run_submission` 一行、
  `runtime.run_record` 一行）；
- 202 回执带 `status`/`execution_status`（来自受理裁决，不再回读 Run）；
- 既有 API 封套/错误码不变（202 成功封套；同键异指纹 409 `IDEMPOTENCY_MISMATCH`）；
- 公开 SSE 可用并跑到终态。
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import GatewayStack

pytestmark = pytest.mark.integration

#: 受理 SLO：与 NFR-PERF-01 同口径（P95 ≤ 500ms）。
SUBMIT_P95_BUDGET_SEC = 0.5
#: 重试采样数：足够算 P95，又不把一条已经受理的消息拖成负载测试。
RETRY_SAMPLES = 20
CHAT_PRINCIPAL_PATH = "/internal/channel/chat-principal"


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _counter_value(prometheus_text: str, *, path: str, status: str = "200") -> float:
    """从真实 `/metrics` 文本里取一条计数器样本；缺失即 0（还没有人调用过）。"""
    for line in prometheus_text.splitlines():
        if not line.startswith("console_api_requests_total{"):
            continue
        if f'path="{path}"' in line and f'status="{status}"' in line:
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def _chat_principal_calls(console_url: str) -> float:
    async with httpx.AsyncClient(base_url=console_url, timeout=10.0, trust_env=False) as console:
        response = await console.get("/metrics")
    assert response.status_code == 200, response.text
    return _counter_value(response.text, path=CHAT_PRINCIPAL_PATH)


async def _scalar(statement: str, params: dict[str, object]) -> int:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()
    return int(value or 0)


async def _exchange_chat_session(stack: GatewayStack) -> tuple[str, str]:
    """真实 Console Token 交换：返回 CHAT Cookie 与上下文身份（浏览器同形）。"""
    async with httpx.AsyncClient(
        base_url=stack.console_url, timeout=15.0, trust_env=False
    ) as console:
        response = await console.post(
            "/api/v1/webchat/auth/exchange", json={"token": stack.chat_token}
        )
    assert response.status_code == 200, response.text
    cookie = response.cookies.get("muad_chat_session")
    assert cookie, "交换必须写独立 CHAT Cookie"
    session_id = str(response.json()["data"]["session_id"])
    return cookie, session_id


def _chat_client(stack: GatewayStack, cookie: str, session_id: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=stack.gateway_url,
        timeout=30.0,
        trust_env=False,
        cookies={"muad_chat_session": cookie},
        headers={"X-Chat-Context": session_id},
    )


async def _create_conversation(client: httpx.AsyncClient, entry_id: uuid.UUID) -> uuid.UUID:
    response = await client.post(
        f"/api/v1/webchat/entries/{entry_id}/conversations",
        headers={"Idempotency-Key": f"conv-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(str(response.json()["data"]["conversation_id"]))


async def _submit(
    client: httpx.AsyncClient,
    entry_id: uuid.UUID,
    *,
    conversation_id: uuid.UUID,
    message_id: str,
    text: str,
    idempotency_key: str,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = await client.post(
        f"/api/v1/webchat/entries/{entry_id}/messages",
        json={
            "conversation_id": str(conversation_id),
            "message_id": message_id,
            "text": text,
            "upload_ids": [],
        },
        headers={"Idempotency-Key": idempotency_key},
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["code"] == "0", payload
    return payload["data"], elapsed


async def test_s08_webchat_sync_admission_is_single_run_and_does_not_re_resolve(
    gateway_stack: GatewayStack,
) -> None:
    stack = gateway_stack
    cookie, session_id = await _exchange_chat_session(stack)
    message_id = f"msg-s08-{uuid.uuid4().hex[:10]}"
    idempotency_key = f"s08::{message_id}"
    text = "S-08 WebChat 统一核心受理"

    async with _chat_client(stack, cookie, session_id) as client:
        conversation_id = await _create_conversation(client, stack.webchat_entry_id)
        baseline_calls = await _chat_principal_calls(stack.console_url)

        accepted, _ = await _submit(
            client,
            stack.webchat_entry_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=text,
            idempotency_key=idempotency_key,
        )
        run_id = uuid.UUID(str(accepted["run_id"]))
        assert uuid.UUID(str(accepted["conversation_id"])) == conversation_id
        # 受理帧直接带状态与阶段投影：新 Run 不需要为回执再读一次 Run/Snapshot。
        assert accepted["status"] == "CREATED", accepted
        assert accepted["execution_status"]["stage"], accepted

        latencies: list[float] = []
        for _ in range(RETRY_SAMPLES):
            replay, elapsed = await _submit(
                client,
                stack.webchat_entry_id,
                conversation_id=conversation_id,
                message_id=message_id,
                text=text,
                idempotency_key=idempotency_key,
            )
            latencies.append(elapsed)
            assert uuid.UUID(str(replay["run_id"])) == run_id, replay
            assert replay["status"] in {
                "CREATED",
                "RUNNING",
                "WAITING_INPUT",
                "WAITING_TOOL",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }, replay
            assert replay["execution_status"]["stage"], replay

        after_calls = await _chat_principal_calls(stack.console_url)
        assert after_calls - baseline_calls == 0, (
            "Redis 会话命中后提交路径不得回源 Console（exchange 写、每请求读 Redis）"
        )

        p95 = _p95(latencies)
        assert p95 <= SUBMIT_P95_BUDGET_SEC, f"202 P95={p95 * 1000:.0f}ms 超过 500ms"

        # 同键异指纹的既有错误码不变（封套语义由真实 Runtime 幂等裁决）。
        conflict = await client.post(
            f"/api/v1/webchat/entries/{stack.webchat_entry_id}/messages",
            json={
                "conversation_id": str(conversation_id),
                "message_id": message_id,
                "text": f"{text}（不同内容）",
                "upload_ids": [],
            },
            headers={"Idempotency-Key": idempotency_key},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH", conflict.text

        # 公开 SSE 可用：打开真实流并读到终态帧（canonical `run.completed` → 公开 `run.terminal`）。
        async with client.stream(
            "GET", f"/api/v1/webchat/runs/{run_id}/events"
        ) as stream:
            assert stream.status_code == 200, await stream.aread()
            body = (await stream.aread()).decode()
        assert "event: run.terminal" in body, body[-500:]

    submissions = await _scalar(
        "SELECT count(*) FROM runtime.run_submission "
        "WHERE tenant_id = :t AND idempotency_key = :k AND is_deleted = false",
        {"t": stack.tenant_id, "k": idempotency_key},
    )
    runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record "
        "WHERE tenant_id = :t AND conversation_id = :c AND is_deleted = false",
        {"t": stack.tenant_id, "c": conversation_id},
    )
    assert submissions == 1, "同一幂等键只允许一条受理记录"
    assert runs == 1, "同一 message_id 的重试不得创建第二个 Run"
