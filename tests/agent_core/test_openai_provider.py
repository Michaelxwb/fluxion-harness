import json
import logging
from typing import Any

import httpx
import pytest
from muad_agent_core.model import (
    ImagePart,
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelRequestError,
    ModelRole,
    ModelToolCall,
    ModelUnavailableError,
    OpenAICompatibleProvider,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect

API_KEY = "sk-secret-value"
BASE_URL = "https://llm.test/v1"


def _request() -> ModelRequest:
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="be helpful"),
            ModelMessage(role=ModelRole.USER, content="hello"),
        ),
        temperature=0.2,
        max_tokens=64,
    )


def _provider(handler: Any, **kwargs: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=BASE_URL,
        model="gpt-4o-mini",
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _tool_choice_payload() -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def _request_with_tools() -> ModelRequest:
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=_request().messages,
        temperature=0.2,
        max_tokens=64,
        tools=(
            ToolDefinition(
                name="echo",
                description="echo",
                input_schema={"type": "object"},
                effect=ToolEffect.READ,
            ),
        ),
    )


async def test_success_maps_choices_tool_calls_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_tool_choice_payload())

    provider = _provider(handler)
    try:
        response = await provider.complete(_request_with_tools())
    finally:
        await provider.aclose()

    assert response.content == ""
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "echo"
    assert response.tool_calls[0].arguments == {"text": "hi"}
    assert (response.input_tokens, response.output_tokens) == (7, 3)


async def test_request_carries_headers_body_and_tool_schema() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        await provider.complete(_request_with_tools())
    finally:
        await provider.aclose()

    assert captured[0].method == "POST"
    assert captured[0].url.path == "/v1/chat/completions"
    assert captured[0].headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][0] == {"role": "system", "content": "be helpful"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["tools"][0]["function"]["name"] == "echo"


async def test_no_api_key_omits_authorization_header() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_tool_choice_payload())

    provider = OpenAICompatibleProvider(
        base_url="http://model.internal/v1",
        model="demo",
        api_key=None,
        transport=httpx.MockTransport(handler),
    )
    try:
        await provider.complete(_request())
    finally:
        await provider.aclose()

    assert len(captured) == 1
    assert "authorization" not in captured[0].headers


async def test_api_key_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        with caplog.at_level(logging.DEBUG):
            response = await provider.complete(_request())
    finally:
        await provider.aclose()

    assert response.content == "ok"
    assert API_KEY not in caplog.text


async def test_rate_limit_reads_retry_after_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2.5"}, text="slow down")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRateLimitedError) as error:
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert error.value.retry_after == 2.5


async def test_rate_limit_without_valid_header_has_no_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "later"}, text="slow down")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRateLimitedError) as error:
            await provider.complete(_request())
    finally:
        await provider.aclose()

    assert error.value.retry_after is None


async def test_server_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_timeout_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_connect_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(ModelUnavailableError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_client_error_maps_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_invalid_json_maps_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_invalid_tool_arguments_map_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {"id": "c1", "function": {"name": "echo", "arguments": "{bad"}}
                            ],
                        }
                    }
                ]
            },
        )

    provider = _provider(handler)
    try:
        with pytest.raises(ModelRequestError):
            await provider.complete(_request())
    finally:
        await provider.aclose()


async def test_external_client_is_not_closed_by_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url=BASE_URL,
        model="gpt-4o-mini",
        api_key=API_KEY,
        client=client,
    )
    provider_typed: ModelProvider = provider
    try:
        await provider_typed.complete(_request())
        await provider.aclose()
        assert client.is_closed is False
    finally:
        await client.aclose()


async def test_stream_emits_text_deltas_and_assembles_tool_calls() -> None:
    """SSE 流式：文本增量逐块回调；分片 tool_calls 组装为完整调用。"""
    chunks = [
        {"choices": [{"delta": {"content": "hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "echo", "arguments": '{"text"'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": ': "hi"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        },
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    provider = _provider(handler)
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    try:
        response = await provider.stream(_request(), on_delta)
    finally:
        await provider.aclose()

    assert deltas == ["hel", "lo"]
    assert response.content == "hello"
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "echo"
    assert dict(response.tool_calls[0].arguments) == {"text": "hi"}
    assert (response.input_tokens, response.output_tokens) == (7, 3)


async def test_stream_maps_rate_limit_before_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"})

    provider = _provider(handler)

    async def on_delta(text: str) -> None:
        raise AssertionError("no deltas expected")

    try:
        with pytest.raises(ModelRateLimitedError) as exc:
            await provider.stream(_request(), on_delta)
    finally:
        await provider.aclose()
    assert exc.value.retry_after == 2.0


# ---------------------------------------------------------------------------
# 多模态内容形态（TASK-005）
#
# 内容类型从「只能是字符串」放宽为可承载内容块。护栏是**纯文本路径逐字节不变**：
# 放宽类型最容易误伤的就是既有对话，所以这里把改造前的请求体整份冻结成基线。
# ---------------------------------------------------------------------------

#: 改造前的纯文本请求体基线（逐字段冻结）。多模态支持**不得**改动这条路径的任何一处。
PLAIN_TEXT_MESSAGES_BASELINE: list[dict[str, Any]] = [
    {"role": "system", "content": "be helpful"},
    {"role": "user", "content": "hello"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"text": "hi"}'},
            }
        ],
    },
    {"role": "tool", "content": "echo: hi", "tool_call_id": "call-1"},
]


def _plain_text_request() -> ModelRequest:
    """覆盖四种角色（含带 tool_calls 的 assistant 与 tool 结果）的纯文本请求。"""
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=(
            ModelMessage(role=ModelRole.SYSTEM, content="be helpful"),
            ModelMessage(role=ModelRole.USER, content="hello"),
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="",
                tool_calls=(ModelToolCall(id="call-1", name="echo", arguments={"text": "hi"}),),
            ),
            ModelMessage(role=ModelRole.TOOL, content="echo: hi", tool_call_id="call-1"),
        ),
    )


async def test_s03_plain_text_request_body_is_unchanged() -> None:
    """S-03：纯文本会话的请求体与改造前**逐字节相同**。

    真实边界：走完整的 provider 组装 + HTTP 序列化（`MockTransport` 只拦截传输，
    不 mock 组装层）——断言的是真正发出去的 body。
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        await provider.complete(_plain_text_request())
    finally:
        await provider.aclose()

    body = json.loads(captured[0].content)
    assert body["messages"] == PLAIN_TEXT_MESSAGES_BASELINE
    # 整份 body 也冻结：新增顶层字段同样属于「改动既有路径」
    assert json.dumps(body, sort_keys=True, ensure_ascii=False) == json.dumps(
        {"model": "gpt-4o-mini", "messages": PLAIN_TEXT_MESSAGES_BASELINE},
        sort_keys=True,
        ensure_ascii=False,
    )


async def test_b06_plain_text_content_stays_a_string() -> None:
    """B-06：内容形态为纯字符串时，输出 `content` 必须是**字符串而非数组**，字段集不变。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler)
    try:
        await provider.complete(_plain_text_request())
    finally:
        await provider.aclose()

    messages = json.loads(captured[0].content)["messages"]
    assert isinstance(messages[0]["content"], str)
    assert set(messages[0]) == {"role", "content"}, "纯文本消息不得新增字段"
    assert isinstance(messages[3]["content"], str)
    assert set(messages[3]) == {"role", "content", "tool_call_id"}


async def test_multimodal_content_becomes_parts_array() -> None:
    """内容为内容块元组时输出 `content` 数组：文本块 + 图像块（TASK-005 的正向能力）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    request = ModelRequest(
        model_id="gpt-4o-mini",
        messages=(
            ModelMessage(
                role=ModelRole.USER,
                content=(
                    "看看这张图",
                    ImagePart(media_type="image/png", data_base64="QUJD"),
                ),
            ),
        ),
    )
    provider = _provider(handler)
    try:
        await provider.complete(request)
    finally:
        await provider.aclose()

    content = json.loads(captured[0].content)["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]


async def test_per_request_timeout_overrides_the_client_default() -> None:
    """[ADR-07] `ModelRequest.timeout_sec` **逐请求**生效，没给就一个字都不覆盖。

    httpx 的 `timeout=None` 是"禁用超时"而不是"用 client 默认值"，所以实现只能按需传键。
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
                ]
            },
        )

    provider = _provider(handler, timeout_sec=30.0)
    await provider.complete(
        ModelRequest(model_id="gpt-4o-mini", messages=_request().messages, timeout_sec=0.02)
    )
    await provider.complete(ModelRequest(model_id="gpt-4o-mini", messages=_request().messages))

    assert seen[0].extensions["timeout"] == {
        "connect": 0.02,
        "read": 0.02,
        "write": 0.02,
        "pool": 0.02,
    }, "调用方给的剩余预算必须落到这次请求上"
    assert seen[1].extensions["timeout"]["read"] == 30.0, "没给就沿用 client 的默认超时"


async def test_stream_without_a_completion_signal_is_not_reported_as_stopped() -> None:
    """[审查 2026-10-06] 流里既没有 `finish_reason` 也没有 `[DONE]` ⇒ 报"没有收尾信息"。

    此前 `finish_reason` 初值是 `"stop"` 且只被非空值覆盖 ⇒ 被截断的流与正常收尾逐字相同，
    残缺回答会被当成功。`[DONE]` 仍在时按正常收尾（有些服务端不发 `finish_reason`）。
    """
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    def _body(chunks: list[dict[str, Any]], tail: str) -> bytes:
        return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + tail).encode()

    truncated = [{"choices": [{"delta": {"content": "说到一半"}}]}]
    done_only = [{"choices": [{"delta": {"content": "说完了"}}]}]

    async def handler(request: httpx.Request) -> httpx.Response:
        body = _body(truncated, "") if len(deltas) == 0 else _body(done_only, "data: [DONE]\n\n")
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = _provider(handler)

    cut = await provider.stream(_request(), on_delta)
    assert cut.finish_reason == "", "没有收尾信号 ⇒ 空串（不得伪装成 stop）"

    finished = await provider.stream(_request(), on_delta)
    assert finished.finish_reason == "stop", "[DONE] 就是收尾信号"
