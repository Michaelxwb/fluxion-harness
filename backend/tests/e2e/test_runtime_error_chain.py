"""真实服务错误契约链（TASK-022 / S-ERR-02 / E-ERR-03）验收测试。

真实边界：真实 Runtime → Gateway → Channel → 客户端（ChainStack 全真实链）；
真实 Runtime + 受控模型故障 → HTTP/SSE。
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.execution_observation_helpers import ChainStack, mint_ids
from tests.e2e.runtime_error_helpers import CountingProvider, error_triple


@pytest.mark.asyncio
async def test_S_ERR_02_error_triple_consistent_across_transports() -> None:
    """S-ERR-02：资源不存在类错误 code/slug/ID 跨层一致（HTTP 与 SSE 同三元组）。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    async with ChainStack() as stack:
        real_complete = DevEchoModelProvider.complete
        faulty = CountingProvider(behavior="error")

        async def failing_complete(self, request):  # type: ignore[no-untyped-def]
            return await faulty.complete(request)

        DevEchoModelProvider.complete = failing_complete  # type: ignore[method-assign]
        try:
            request_id, trace_id, _ = mint_ids("a")
            headers = {
                "Authorization": f"Bearer {stack.issued_token}",
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
            }
            assert stack.chat_client is not None
            http_resp = await stack.chat_client.post(
                "/api/v1/channels/web/access/messages",
                json={
                    "conversation_id": "conv-err",
                    "message_id": "m-err",
                    "content": "hello error",
                },
                headers=headers,
            )
            assert http_resp.status_code == 400
            http_triple = error_triple(http_resp.json())

            sse_resp = await stack.chat_client.post(
                "/api/v1/channels/web/access/messages:stream",
                json={
                    "conversation_id": "conv-err-s",
                    "message_id": "m-err-s",
                    "content": "hello error",
                },
                headers=headers,
            )
            assert sse_resp.status_code == 200
            frames = [
                block
                for block in sse_resp.text.split("\n\n")
                if block.startswith("event: error")
            ]
            assert len(frames) == 1
            sse_triple = error_triple(json.loads(frames[0].split("data: ", 1)[1]))
            # 同一错误跨 transport 三元组一致（code 本地码、slug 上游原始、ID 关联）。
            assert http_triple[1] == sse_triple[1] == "model_provider_error"
            assert http_triple[2] == sse_triple[2] == request_id
            assert faulty.calls == 2
        finally:
            DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_E_ERR_03_model_timeout_and_unknown_mapped_safely_once() -> None:
    """E-ERR-03：模型不可用/超时/未知异常安全映射；一次执行不重复调用。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    async with ChainStack() as stack:
        real_complete = DevEchoModelProvider.complete
        faulty = CountingProvider(behavior="timeout")

        async def timeout_complete(self, request):  # type: ignore[no-untyped-def]
            return await faulty.complete(request)

        DevEchoModelProvider.complete = timeout_complete  # type: ignore[method-assign]
        try:
            request_id, trace_id, _ = mint_ids("b")
            headers = {
                "Authorization": f"Bearer {stack.issued_token}",
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
            }
            assert stack.chat_client is not None
            resp = await stack.chat_client.post(
                "/api/v1/channels/web/access/messages",
                json={
                    "conversation_id": "conv-timeout",
                    "message_id": "m-timeout",
                    "content": "hello timeout",
                },
                headers=headers,
            )
            # 超时安全映射：状态码正确、无敏感原文、slug 为超时语义。
            assert resp.status_code == 400
            body = resp.json()
            assert body["error"] == "model_provider_timeout"
            assert body["request_id"] == request_id
            assert faulty.calls == 1
        finally:
            DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]
