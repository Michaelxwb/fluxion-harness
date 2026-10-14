"""模型探针的 `/script` 契约：坏脚本当场 400，不要等到模型调用时才 500。

`delay_ms` 由白名单按键取值，不校验形状；探针的脚本又是**模块级全局**（跨用例留存），
所以一旦写进一个非数值，后续每个用例的模型调用都会炸在 `int()` 上——那时看起来像模型侧挂了。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from tests.e2e.openai_probe_app import app


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as instance:
        try:
            yield instance
        finally:
            await instance.post("/script", json={})  # 脚本是模块级全局：用完必须清


async def test_script_rejects_a_delay_that_is_not_a_non_negative_integer(
    client: httpx.AsyncClient,
) -> None:
    for payload in ({"delay_ms": None}, {"delay_ms": "1500"}, {"delay_ms": -1}):
        response = await client.post("/script", json=payload)
        assert response.status_code == 400, (payload, response.text)

    accepted = await client.post("/script", json={"delay_ms": 1500})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["script"]["delay_ms"] == 1500


async def _complete(client: httpx.AsyncClient, messages: list[dict[str, str]]) -> str:
    response = await client.post("/v1/chat/completions", json={"messages": messages})
    assert response.status_code == 200, response.text
    return str(response.json()["choices"][0]["message"]["content"])


async def test_tool_once_skips_the_repeated_tool_call_after_a_resumed_round(
    client: httpx.AsyncClient,
) -> None:
    """暂停/resume：恢复输入把工具结果推到"最后一条 user 消息"之前，tool_once 让探针不再重复要工具。"""
    await client.post(
        "/script",
        json={"tool_name": "remember", "tool_arguments": "{}", "final_text": "已记下。", "tool_once": True},
    )
    first = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "以后都用中文回答我"}]},
    )
    assert first.json()["choices"][0]["finish_reason"] == "tool_calls"

    resumed = await client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "以后都用中文回答我"},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "[External tool data: {\"saved\": false}]"},
                {"role": "user", "content": "确认 5c0719f4-00b6-40c4-b4e7-31278135238b"},
            ]
        },
    )
    body = resumed.json()["choices"][0]
    assert body["finish_reason"] == "stop"
    assert body["message"]["content"] == "已记下。"

    # 未置 tool_once 时保持既有口径：恢复轮仍会再调一次工具
    await client.post("/script", json={"tool_name": "remember", "tool_arguments": "{}"})
    again = await client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "[External tool data: {\"saved\": false}]"},
                {"role": "user", "content": "确认 5c0719f4-00b6-40c4-b4e7-31278135238b"},
            ]
        },
    )
    assert again.json()["choices"][0]["finish_reason"] == "tool_calls"


async def test_consolidation_text_applies_only_to_the_consolidation_call_and_resolves_placeholders(
    client: httpx.AsyncClient,
) -> None:
    """记忆提炼要严格 JSON 批次：脚本只作用于提炼回合，占位取自本次请求体。"""
    conversation = "8f14e45f-ceea-467a-9c0b-3f0f0f0f0f0f"
    batch = (
        '{"candidates":[{"memory_key":"$root_memory_key","category":"TASK_STATE","content":'
        '{"title":"季度报告","goal":"补齐数据","constraints":[],"open_items":[],"next_action":"核验",'
        '"root_conversation_id":"$root_conversation_id","source_conversation_id":"$source_conversation_id"}}],'
        '"task_completed":false}'
    )
    await client.post("/script", json={"final_text": "业务回合", "consolidation_text": batch})
    instructions = {
        "role": "system",
        "content": (
            "Consolidate this frozen conversation input."
            f" Current source conversation is {conversation}."
        ),
    }
    payload = {"role": "user", "content": '{"messages":[],"resume_ref":null}'}

    consolidated = await _complete(client, [instructions, payload])
    assert conversation in consolidated
    assert f"task.{conversation.replace('-', '')}" in consolidated
    # 业务回合不受 consolidation_text 影响
    assert await _complete(client, [{"role": "user", "content": "你好"}]) == "业务回合"

    root = "11111111-2222-3333-4444-555555555555"
    await client.post(
        "/script",
        json={
            "consolidation_text": '{"memory_key":"$root_memory_key","root":"$root_conversation_id"}',
        },
    )
    resumed = await _complete(
        client,
        [
            instructions,
            {
                "role": "user",
                "content": (
                    '{"messages":[],"resume_ref":{"memory_key":"task.original",'
                    f'"root_conversation_id":"{root}"}}}}'
                ),
            },
        ],
    )
    assert root in resumed and "task.original" in resumed
