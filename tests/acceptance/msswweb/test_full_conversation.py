"""S-03 完整对话（MSSWWEB 全链路，真实 Console/Runtime/Worker/LLM 探针）。

这是整个需求最关键的一条：MSSW 的用户从「拿到权限」到「真的聊完一轮」，全程经
`soc-token` 认证、真实 Agent 执行、SSE 流式返回。它跑在真实栈上，所以能发现单测
发现不了的问题——比如网关认证通过但 Runtime 侧会话归属错、SSE 提前结束、历史读不回。

设计事实（决定了步骤顺序）：
- `/identity` 只回答权限，不建任何东西（幂等、可随时调用）；
- 建会话与发消息都要 `Idempotency-Key`（路由级必填头），去重依据是 `message_id`；
- 事件流用 `event: run.terminal` 判定终态（与 WebChat 面同一套 EventBus 协议）。
"""

from __future__ import annotations

import uuid

import httpx

from tests.acceptance.msswweb.conftest import MsswStack
from tests.e2e.mssw_token_factory import issue

IDENTITY = "/api/v1/msswweb/identity"
ENTRIES = "/api/v1/msswweb/entries"


def _token(mssw_stack: MsswStack) -> str:
    return issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )


def test_s03_full_conversation_over_mssw_surface(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    headers = mssw_stack.mssw_headers(_token(mssw_stack))
    # 事件流可能等真实模型输出，放宽读超时（httpx 默认 30s 对长流不够）
    gateway_client.timeout = httpx.Timeout(30.0, read=180.0)
    base = mssw_stack.gateway_url
    entry_id = mssw_stack.seed.entry_id

    # 1) 权限查询：允许，且 grants 指向本入口
    identity = gateway_client.get(f"{base}{IDENTITY}", headers=headers)
    assert identity.status_code == 200, identity.text
    identity_data = identity.json()["data"]
    assert identity_data["allowed"] is True
    assert str(entry_id) in {grant["entry_id"] for grant in identity_data["grants"]}

    # 2) 入口列表：只列可用入口，且带 no-store（凭据不进缓存）
    entries = gateway_client.get(f"{base}{ENTRIES}", headers=headers)
    assert entries.status_code == 200, entries.text
    assert "no-store" in entries.headers.get("cache-control", "").lower()
    assert str(entry_id) in {item["entry_id"] for item in entries.json()["data"]["items"]}

    # 3) 建会话
    created = gateway_client.post(
        f"{base}{ENTRIES}/{entry_id}/conversations",
        headers={**headers, "Idempotency-Key": f"s03-conv-{uuid.uuid4().hex[:8]}"},
        json={},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["data"]["conversation_id"]

    # 4) 发消息：202 + run_id
    submitted = gateway_client.post(
        f"{base}{ENTRIES}/{entry_id}/messages",
        headers={**headers, "Idempotency-Key": f"s03-msg-{uuid.uuid4().hex[:8]}"},
        json={
            "conversation_id": conversation_id,
            "message_id": f"m-{uuid.uuid4().hex[:8]}",
            "text": "你好，请用一句话介绍你自己。",
        },
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["data"]["run_id"]
    assert run_id, submitted.text

    # 5) 事件流：必须有终态帧，且连接自然结束（不是被超时掐断）
    with gateway_client.stream(
        "GET", f"{base}/api/v1/msswweb/runs/{run_id}/events", headers=headers
    ) as stream:
        assert stream.status_code == 200, stream.read()
        body = stream.read().decode("utf-8")
    assert "event: run.terminal" in body, body[-800:]

    # 6) 历史：用户消息与助手回答都在，且刷新后仍然读得回
    history = gateway_client.get(
        f"{base}/api/v1/msswweb/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert history.status_code == 200, history.text
    roles = [item.get("role") for item in history.json()["data"]["items"]]
    assert "USER" in roles, history.text
    assert "ASSISTANT" in roles, history.text

    # 7) 响应不得回显断言原文
    token = _token(mssw_stack)
    echo_headers = mssw_stack.mssw_headers(token)
    echo = gateway_client.get(f"{base}{IDENTITY}", headers=echo_headers)
    assert token not in echo.text, "断言原文不得出现在响应体里"
