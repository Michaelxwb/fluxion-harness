"""会话与消息边界（B-01、B-03、E-06）：MSSWWEB 用户视角的「能不能正常开始聊」。

这三条都有共同点：**不需要 LLM 出长回答**即可判定，因此放在 pytest 持续回归里，
而把需要真实模型与 SSE 时序的场景（S-02/S-03/S-05、E-07/E-08）交给浏览器层。
"""

from __future__ import annotations

import uuid

import httpx

from tests.acceptance.msswweb.conftest import FreshUser, MsswStack
from tests.e2e.mssw_token_factory import issue

ENTRIES = "/api/v1/msswwweb/entries"
CLIENT_CONFIG = "/api/v1/msswwweb/client-config"


def _headers(mssw_stack: MsswStack) -> dict[str, str]:
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    return mssw_stack.mssw_headers(token)


def _entry_path(mssw_stack: MsswStack, suffix: str) -> str:
    return f"{ENTRIES}/{mssw_stack.seed.entry_id}/{suffix}"


def test_b01_new_user_sees_empty_conversation_list(
    mssw_stack: MsswStack, gateway_client: httpx.Client, fresh_mssw_user: FreshUser
) -> None:
    """从未聊过的人拿到的必须是 `items=[]`，而不是报错或隐式建会话。

    用 `fresh_mssw_user`（新平台用户）而不是种子身份：会话按平台用户归属，种子用户在同
    模块其它用例里已经建过会话，复用会让「不空」看起来像缺陷。
    """
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{_entry_path(mssw_stack, 'conversations')}",
        headers=mssw_stack.mssw_headers(fresh_mssw_user.token(mssw_stack.platform)),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == []


def test_b01_message_without_conversation_id_is_rejected(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """没有 `conversation_id` 的提交必须被拒（422），不隐式建会话。

    幂等键照常携带，确保 422 的原因是**缺会话**而不是缺头——否则这条用例就失去意义。
    """
    response = gateway_client.post(
        f"{mssw_stack.gateway_url}{_entry_path(mssw_stack, 'messages')}",
        headers={**_headers(mssw_stack), "Idempotency-Key": f"b01-{uuid.uuid4().hex[:8]}"},
        json={"message_id": f"m-{uuid.uuid4().hex[:8]}", "text": "hello"},
    )

    assert response.status_code == 422, response.text
    errors = response.json()["data"]["errors"]
    assert any(error["loc"][-1] == "conversation_id" for error in errors), (
        f"拒绝原因必须是缺 conversation_id，实际: {errors}"
    )


def test_b03_client_config_exposes_text_limit_for_client_side_guard(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """超限文本由**客户端**拦截：服务端只暴露上限，不在网关做长度校验。

    实测事实（2026-11）：`text_max_bytes` 仅出现在 `client-config` 与设置元数据里，
    `webchat.py` 的提交路由不做长度判断，超限文本会被受理（202）。因此本用例断言的是
    「上限确实下发给客户端」，而不是「服务端会拒」。

    这是**已知缺口**（与本次 MSSWWEB 接入无关，WebChat 面同样如此）：若要服务端兜底，
    应在 `submit_message` 里按 `platform_settings.webchat.text_max_bytes` 校验并回
    `VALIDATION_FAILED`。此处不擅自改跨通道行为，记录在案。
    """
    headers = _headers(mssw_stack)
    config = gateway_client.get(f"{mssw_stack.gateway_url}{CLIENT_CONFIG}", headers=headers)

    assert config.status_code == 200, config.text
    limit = config.json()["data"]["webchat"]["text_max_bytes"]
    assert isinstance(limit, int) and limit > 0


def test_e06_duplicate_message_id_does_not_create_second_run(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """同一 `message_id` 连发两次：绝不产生第二个执行。

    实测终态（2026-11）：首次 202 建 Run；紧接着重发得到 **409 `RUN_BUSY`**——会话正在执行，
    新提交被拒。也就是说「重复提交」的防护落在**会话级并发闸**上，而不是「返回同一个 run_id」。
    两者都满足「不会跑两遍」，本用例接受这两种终态，但拒绝「两个不同 run_id 同时受理」。
    """
    headers = _headers(mssw_stack)
    conversation = gateway_client.post(
        f"{mssw_stack.gateway_url}{_entry_path(mssw_stack, 'conversations')}",
        # 建会话是可信写操作，必须带幂等键；发消息用 `message_id` 去重，不需要它。
        headers={**headers, "Idempotency-Key": f"e06-conv-{uuid.uuid4().hex[:8]}"},
        json={},
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["data"]["conversation_id"]

    body = {
        "conversation_id": conversation_id,
        "message_id": f"m-{uuid.uuid4().hex[:8]}",
        "text": "hi",
    }
    # 发消息也必须带幂等键（路由级 `Header(min_length=1)`）；但**去重**依据是 body 里的
    # `message_id`——两次用不同的键、同一 `message_id`，仍须复用同一 Run。
    first = gateway_client.post(
        f"{mssw_stack.gateway_url}{_entry_path(mssw_stack, 'messages')}",
        headers={**headers, "Idempotency-Key": f"e06-a-{uuid.uuid4().hex[:8]}"},
        json=body,
    )
    second = gateway_client.post(
        f"{mssw_stack.gateway_url}{_entry_path(mssw_stack, 'messages')}",
        headers={**headers, "Idempotency-Key": f"e06-b-{uuid.uuid4().hex[:8]}"},
        json=body,
    )

    assert first.status_code == 202, first.text
    if second.status_code == 202:
        assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"], (
            "同一 message_id 不得开出第二个 Run"
        )
    else:
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "RUN_BUSY", "并发提交必须被会话级闸拦下，而不是静默排队再跑一遍"
