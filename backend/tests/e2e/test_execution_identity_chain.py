"""身份全链路 E2E（TASK-007 / S-ID-03）验收测试。

真实边界：真实 Channel App → 真实 HttpRuntimeGateway →
真实 Runtime FastAPI → 真实 service（dev.echo）→ PG + Memory/Trace。
不断言未实现的东西：started/completed 经 Channel SSE 事件观测；
Memory 经注入的 memory_store.read_l1；Trace 经 runtime trace_store。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.e2e.execution_observation_helpers import ChainStack, mint_ids


@pytest.mark.asyncio
async def test_S_ID_03_identity_propagates_end_to_end() -> None:
    """S-ID-03：各观测点关联同一身份（Channel 结果 / Trace / Memory / SSE）。"""
    async with ChainStack() as stack:
        request_id, trace_id, _ = mint_ids("a")
        result_data, _ = await stack.chat(
            "hello chain",
            request_id=request_id,
            trace_id=trace_id,
            stream=False,
        )
        assert result_data["request_id"] == request_id
        assert result_data["trace_id"] == trace_id
        execution_id = result_data["execution_id"]
        assert execution_id.startswith("exec_")

        record = await stack.runtime.trace_store.get(trace_id)
        assert record is not None
        assert record.execution_id == execution_id
        assert record.snapshot.agent_definition_id == "assistant"

        messages = await stack.memory_store.read_l1("tenant-a", "conv-chain")
        contents = [m.content for m in messages]
        assert "hello chain" in contents
        assert any(c.startswith("dev: ") for c in contents)

        _, sse_text = await stack.chat(
            "hello stream",
            request_id=request_id,
            trace_id=trace_id,
            conversation="conv-chain-sse",
        )
        assert f'"request_id": "{request_id}"' in sse_text
        assert '"execution_id": "exec_' in sse_text


@pytest.mark.asyncio
async def test_S_ID_03_concurrent_executions_isolated() -> None:
    """S-ID-03：并发无串扰——两执行身份各自贯通、Trace 分离。"""
    async with ChainStack() as stack:
        req_a, trace_a, _ = mint_ids("b")
        req_b, trace_b, _ = mint_ids("c")
        (res_a, _), (res_b, _) = await asyncio.gather(
            stack.chat("first", request_id=req_a, trace_id=trace_a, conversation="conv-a", stream=False),
            stack.chat("second", request_id=req_b, trace_id=trace_b, conversation="conv-b", stream=False),
        )
        assert res_a["execution_id"] != res_b["execution_id"]
        assert res_a["request_id"] == req_a
        assert res_b["request_id"] == req_b
        rec_a = await stack.runtime.trace_store.get(trace_a)
        rec_b = await stack.runtime.trace_store.get(trace_b)
        assert rec_a is not None and rec_b is not None
        assert rec_a.execution_id == res_a["execution_id"]
        assert rec_b.execution_id == res_b["execution_id"]


@pytest.mark.asyncio
async def test_S_ID_03_unbound_rejected_and_secrets_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S-ID-03：未绑定执行被拒绝；token/secret 不进日志。"""
    caplog.set_level(logging.INFO)
    async with ChainStack() as stack:
        request_id, trace_id, _ = mint_ids("d")
        denied = await stack.chat_client.post(
            "/api/v1/channels/web/access/messages",
            json={
                "conversation_id": "conv-x",
                "message_id": "m-x",
                "content": "hello",
            },
            headers={
                "Authorization": "Bearer #/deadbeef",
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
            },
        )
        assert denied.status_code in (401, 403)

        request_id2, trace_id2, _ = mint_ids("e")
        result_data, _ = await stack.chat(
            "hello secret-check",
            request_id=request_id2,
            trace_id=trace_id2,
        )
        assert result_data["request_id"] == request_id2

    text = "\n".join(
        f"{record.getMessage()} {record.args}" for record in caplog.records
    )
    assert stack.issued_token not in text
    assert "deadbeef" not in text


@pytest.mark.asyncio
async def test_S_ID_03_invalid_entry_identity_rejected() -> None:
    """S-ID-03：channel 入口任意 ID fail-closed（400 + slug，不进执行）。"""
    async with ChainStack() as stack:
        denied = await stack.chat_client.post(
            "/api/v1/channels/web/access/messages",
            json={
                "conversation_id": "conv-y",
                "message_id": "m-y",
                "content": "hello",
            },
            headers={
                "Authorization": f"Bearer {stack.issued_token}",
                "X-Request-ID": "req-legacy-client",
                "X-Trace-ID": "trace-legacy-client",
            },
        )
        assert denied.status_code == 400
        assert denied.json()["error"] == "request_identity_invalid"
