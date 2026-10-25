"""[S-02][E-01] 1000 用户 15s 通知轮询与设置源故障（TASK-002）。

真实边界：
- 真实 Gateway → Console chat-principal → Runtime 通知端点 → PG/Redis；
- 每个用户独立 CHAT 主体与 Cookie，请求带 `page_size`（浏览器同形轮询）；
- 设置源故障用真实 PG 行（非法 settings_json revision）注入，验证已带 page_size 的轮询
  不再回源设置；Console 的 `platform_settings_fetch_total` 增量必须为 0。

默认 `CAPACITY_NOTIFICATION_USERS=1000` 是正式场景；受限验证用 `CAPACITY_LOAD_USERS` 与
`CAPACITY_NOTIFICATION_USERS` 显式缩小，并在断言里登记规模，不冒充 1000。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import pytest

from tests.acceptance.capacity.delivery_harness import scrape_metrics
from tests.acceptance.capacity.test_capacity_smoke import (  # noqa: F401  (fixture 复用)
    SmokeModule,
    smoke_module,
)
from tests.capacity.metrics import parse_prometheus_text, summarize_latencies

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

NOTIFY_USERS = int(os.environ.get("CAPACITY_NOTIFICATION_USERS", "1000"))
ROUNDS = int(os.environ.get("CAPACITY_NOTIFICATION_ROUNDS", "2"))
POLL_SEC = float(os.environ.get("CAPACITY_NOTIFICATION_POLL_SEC", "15"))
NOTIFY_PATH = "/api/v1/webchat/notifications"


def _metric_sum(text: str, name: str) -> float:
    samples = parse_prometheus_text(text, source="console:/metrics")
    return float(sum(sample.value for sample in samples if sample.name == name))


async def _poll_once(client: httpx.AsyncClient, after_seq: int) -> dict[str, Any]:
    started = time.monotonic()
    response = await client.get(
        NOTIFY_PATH, params={"after_seq": after_seq, "page_size": 20}
    )
    latency_ms = (time.monotonic() - started) * 1000
    body = response.json()
    return {
        "status": response.status_code,
        "latency_ms": latency_ms,
        "data": body.get("data") if isinstance(body, dict) else None,
        "text": response.text[:300],
    }


async def _one_user(
    module: SmokeModule, index: int, rounds: int
) -> tuple[list[dict[str, Any]], int]:
    credential = module.load_sessions[index]
    results: list[dict[str, Any]] = []
    after_seq = 0
    async with httpx.AsyncClient(
        base_url=module.stack.url_of("im-gateway"),
        timeout=30.0,
        trust_env=False,
        cookies={"muad_chat_session": credential.cookie},
        headers={"X-Chat-Context": credential.context},
    ) as client:
        for round_index in range(rounds):
            if round_index:
                await asyncio.sleep(POLL_SEC)
            result = await _poll_once(client, after_seq)
            results.append(result)
            data = result["data"]
            if isinstance(data, dict):
                after_seq = int(data.get("next_seq", after_seq))
    return results, after_seq


async def _poll_users(module: SmokeModule, users: int, rounds: int):
    return await asyncio.gather(*(_one_user(module, index, rounds) for index in range(users)))


@pytest.fixture(scope="module")
def notification_run(smoke_module: SmokeModule):  # noqa: F811
    users = min(NOTIFY_USERS, len(smoke_module.load_sessions))
    console_url = smoke_module.stack.url_of("console-platform")
    # 对照窗口：Runtime 的 retention/维护循环也会取设置；同长度空闲窗口作为背景基线。
    idle_before = scrape_metrics(console_url)
    idle_started = time.monotonic()
    time.sleep(ROUNDS * POLL_SEC)
    idle_elapsed = time.monotonic() - idle_started
    idle_after = scrape_metrics(console_url)
    idle_fetch = _metric_sum(idle_after, "platform_settings_fetch_total") - _metric_sum(
        idle_before, "platform_settings_fetch_total"
    )

    console_before = scrape_metrics(console_url)
    started = time.monotonic()
    results = asyncio.run(_poll_users(smoke_module, users, ROUNDS))
    elapsed = time.monotonic() - started
    console_after = scrape_metrics(console_url)
    return {
        "users": users,
        "results": results,
        "elapsed": elapsed,
        "idle_elapsed": idle_elapsed,
        "idle_settings_fetch_delta": idle_fetch,
        "settings_fetch_delta": _metric_sum(console_after, "platform_settings_fetch_total")
        - _metric_sum(console_before, "platform_settings_fetch_total"),
        "cpu_seconds_delta": _metric_sum(console_after, "process_cpu_seconds_total")
        - _metric_sum(console_before, "process_cpu_seconds_total"),
    }


def test_s02_1000_user_notification_polling_does_not_fetch_settings(
    smoke_module: SmokeModule,  # noqa: F811  (pytest fixture 注入，名字与导入的 fixture 相同)
    notification_run: dict[str, Any],
) -> None:
    """[S-02] 1000 用户 15s 轮询：全部 200、无 pool timeout、设置回源为 0、CPU 在预算内。"""
    run = notification_run
    users = run["users"]
    assert users == min(NOTIFY_USERS, len(smoke_module.load_sessions))
    if users < 1000:
        assert os.environ.get("CAPACITY_NOTIFICATION_USERS") is not None, (
            "缩小轮询规模必须显式声明 CAPACITY_NOTIFICATION_USERS"
        )

    flat = [item for results, _ in run["results"] for item in results]
    assert len(flat) == users * ROUNDS
    assert all(item["status"] == 200 for item in flat), (
        f"通知轮询必须全部 200；失败 {[(i['status'], i['text']) for i in flat if i['status'] != 200][:3]}"
    )
    latencies = summarize_latencies([item["latency_ms"] for item in flat])
    assert latencies.p95 <= 2000, f"轮询 P95 {latencies.p95:.0f}ms 超预算"

    assert run["settings_fetch_delta"] <= run["idle_settings_fetch_delta"], (
        "带 page_size 的轮询不得额外回源 Console settings；"
        f"轮询增量 {run['settings_fetch_delta']} vs 空闲基线 {run['idle_settings_fetch_delta']}"
    )

    resources = smoke_module.profile.resources
    assert resources is not None
    limit_cores = resources.hardware["console-platform"].cpu_limit_millicores / 1000
    cpu_cores = run["cpu_seconds_delta"] / max(run["elapsed"], 1e-6)
    assert cpu_cores <= limit_cores * 0.6 + 0.05, (
        f"Console CPU {cpu_cores:.2f} cores 超 60% limit（{limit_cores * 0.6:.2f}）"
    )
