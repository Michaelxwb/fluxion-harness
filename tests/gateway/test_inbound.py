from __future__ import annotations

from uuid import uuid4

import asyncio
from contextlib import suppress
from pathlib import Path

from fakes import FakeConsoleClient, FakeRuntimeClient, make_envelope, resolved_response
from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import ChannelResolveRequest, ChannelResolveResponse, DeliveryMessage, DeliveryRouteInput
from muad_im_gateway.application.inbound import (
    BIND_USAGE_TEXT,
    BROKEN_STREAM_TEXT,
    DELTA_FLUSH_CHARS,
    NEW_CONVERSATION_TEXT,
    NO_PERMISSION_TEXT,
    NO_SKILLS_TEXT,
    SKILLS_UNAVAILABLE_TEXT,
    STOP_ACCEPTED_TEXT,
    UNBOUND_TEXT,
    InboundPipeline,
)
from muad_im_gateway.application.runtime_client import SseEvent
from muad_im_gateway.channels.base import ChannelAdapterUnavailable
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    DedupeStore,
    DedupeStoreError,
    InMemoryDedupeStore,
    NullDedupeStore,
)


class _BrokenSendAdapter(FakeChannelAdapter):
    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        raise ChannelAdapterUnavailable("sdk missing")

    async def stream(self, route: DeliveryRouteInput, chunks) -> None:  # type: ignore[no-untyped-def]
        async for _chunk in chunks:
            pass
        raise ChannelAdapterUnavailable("sdk missing")


class _FinalizingAdapter(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.finished: list[DeliveryRouteInput] = []

    async def finish_stream(self, route: DeliveryRouteInput) -> None:
        self.finished.append(route)


class _BrokenDedupeStore:
    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        raise DedupeStoreError("redis down")

    async def aclose(self) -> None:
        return None


class _RecordingDedupeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        self.calls.append((key, ttl_sec))
        return True

    async def aclose(self) -> None:
        return None


def _pipeline(
    console: FakeConsoleClient,
    runtime: FakeRuntimeClient,
    *,
    catalog: MessageCatalog,
    dedupe: DedupeStore | None = None,
) -> InboundPipeline:
    return InboundPipeline(
        dedupe=dedupe if dedupe is not None else InMemoryDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id="tenant-1",
        locale="zh-CN",
    )


def _sent_texts(adapter: FakeChannelAdapter) -> list[str]:
    return [message.text for _route, message in adapter.sent]


def _streamed_chunks(adapter: FakeChannelAdapter) -> list[tuple[str, ...]]:
    return [chunks for _route, chunks in adapter.streamed]


async def test_unbound_message_prompts_bind(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response(bound=False)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _sent_texts(adapter) == [UNBOUND_TEXT]
    assert runtime.run_requests == []


async def test_unauthorized_message_replies_denied(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response(bound=True, authorized=False)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _sent_texts(adapter) == [NO_PERMISSION_TEXT]
    assert runtime.run_requests == []


async def test_authorized_message_dispatches_run(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1", "resumed": False}),
            SseEvent(type="message.delta", data={"delta": "你"}),
            SseEvent(type="message.delta", data={"delta": "好"}),
            SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "你好"}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="你好"))

    assert len(runtime.run_requests) == 1
    request = runtime.run_requests[0]
    assert request.agent_id == console.resolve_response.agent_id
    assert request.platform_user_id == console.resolve_response.platform_user_id
    assert request.channel.bot_id == "bot-1"
    assert request.channel.external_conversation_id == "conv-1"
    assert request.message.id == "msg-1"
    assert request.message.text == "你好"
    assert _streamed_chunks(adapter) == [("你好",)]
    assert _sent_texts(adapter) == []


async def test_run_completed_finalizes_stream(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="message.delta", data={"delta": "你好"}),
            SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "你好"}),
        ]
    )
    adapter = _FinalizingAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _streamed_chunks(adapter) == [("你好",)]
    assert len(adapter.finished) == 1
    assert adapter.finished[0].bot_id == "bot-1"


async def test_delta_throttle_batches_consecutive_deltas(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    first, second, third = "a" * 30, "b" * 30, "c" * 30
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="message.delta", data={"delta": first}),
            SseEvent(type="message.delta", data={"delta": second}),
            SseEvent(type="message.delta", data={"delta": third}),
            SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "done"}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert sum(len(chunk) for chunk in (first, second, third)) > DELTA_FLUSH_CHARS
    assert _streamed_chunks(adapter) == [(first + second,), (third,)]


async def test_task_accepted_path_sends_message_and_ends(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="message.delta", data={"delta": "部分输出"}),
            SseEvent(
                type="task.accepted",
                data={
                    "task_id": "task-1",
                    "status": "QUEUED",
                    "delivery_mode": "FINAL_ONLY",
                    "message": "任务已受理，完成后会通知你",
                },
            ),
            SseEvent(type="message.delta", data={"delta": "应当被忽略"}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _streamed_chunks(adapter) == [("部分输出",)]
    assert _sent_texts(adapter) == ["任务已受理，完成后会通知你"]


async def test_interrupt_required_flushes_and_sends_prompt(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-9"}),
            SseEvent(type="message.delta", data={"delta": "分析中"}),
            SseEvent(
                type="interrupt.required",
                data={
                    "interrupt_id": "int-1",
                    "kind": "CONFIRM",
                    "prompt": "将对 2 台设备执行策略检查，是否继续？",
                    "options": ["继续", "取消"],
                },
            ),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _streamed_chunks(adapter) == [("分析中",)]
    assert _sent_texts(adapter) == ["将对 2 台设备执行策略检查，是否继续？"]
    assert pipeline.pending_run_id(console.resolve_response.platform_user_id) == "run-9"


async def test_stream_ending_without_terminal_event_replies_broken(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="message.delta", data={"delta": "半句"}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _streamed_chunks(adapter) == [("半句",)]
    assert _sent_texts(adapter) == [BROKEN_STREAM_TEXT]


async def test_run_failed_uses_catalog_error_code(tmp_path: Path) -> None:
    messages_file = tmp_path / "messages.yaml"
    messages_file.write_text(
        "codes:\n"
        '  "0":\n'
        "    http_status: 200\n"
        "    messages:\n"
        "      zh-CN: 成功\n"
        "      en-US: Success\n"
        "  COMMON_INTERNAL_ERROR:\n"
        "    http_status: 500\n"
        "    messages:\n"
        "      zh-CN: 系统内部错误\n"
        "      en-US: Internal server error\n"
        "  MODEL_UNAVAILABLE:\n"
        "    http_status: 503\n"
        "    messages:\n"
        "      zh-CN: 服务暂时繁忙，请稍后重试\n"
        "      en-US: The model is temporarily unavailable\n",
        encoding="utf-8",
    )
    custom_catalog = MessageCatalog(messages_file)
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="run.failed", data={"status": "FAILED", "error_code": "MODEL_UNAVAILABLE"}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=custom_catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _sent_texts(adapter) == ["服务暂时繁忙，请稍后重试"]


async def test_run_busy_error_envelope_replies_catalog_text(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient()
    runtime.run_error = AppError(ErrorCode.RUN_BUSY)
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _sent_texts(adapter) == ["当前会话已有任务执行中，可发送 /stop 停止"]


async def test_console_resolve_failure_replies_internal_error(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())

    assert _sent_texts(adapter) == ["系统内部错误"]
    assert runtime.run_requests == []


async def test_duplicate_message_is_ignored(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient([])
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)
    envelope = make_envelope(message_id="dup-1")

    await pipeline.handle(adapter, envelope)
    await pipeline.handle(adapter, envelope)

    assert len(console.resolve_calls) == 1
    assert len(runtime.run_requests) == 1


async def test_dedupe_failure_degrades_to_at_least_once(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient([])
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog, dedupe=_BrokenDedupeStore())

    await pipeline.handle(adapter, make_envelope())

    assert len(runtime.run_requests) == 1


async def test_dedupe_key_uses_channel_and_message_id_with_ttl(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient([])
    adapter = FakeChannelAdapter()
    store = _RecordingDedupeStore()
    pipeline = _pipeline(console, runtime, catalog=catalog, dedupe=store)

    await pipeline.handle(adapter, make_envelope(message_id="msg-42"))

    assert store.calls == [("im:dedupe:WECOM:msg-42", 600)]


async def test_null_dedupe_store_stays_at_least_once(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient([])
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog, dedupe=NullDedupeStore())
    envelope = make_envelope(message_id="null-1")

    await pipeline.handle(adapter, envelope)
    await pipeline.handle(adapter, envelope)

    assert len(runtime.run_requests) == 2


async def test_bind_success(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/bind ABC123"))

    assert _sent_texts(adapter) == ["绑定成功"]
    assert len(console.bind_calls) == 1
    assert console.bind_calls[0].bind_code == "ABC123"


async def test_bind_invalid_code(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.bind_error = AppError(ErrorCode.BIND_CODE_INVALID)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/bind BAD"))

    assert _sent_texts(adapter) == ["绑定码无效"]


async def test_bind_expired_code(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.bind_error = AppError(ErrorCode.BIND_CODE_EXPIRED)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/bind OLD"))

    assert _sent_texts(adapter) == ["绑定码已过期"]


async def test_bind_without_code_shows_usage(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/bind"))

    assert _sent_texts(adapter) == [BIND_USAGE_TEXT]
    assert console.bind_calls == []


async def test_stop_with_active_run_accepted(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/stop"))

    assert _sent_texts(adapter) == [STOP_ACCEPTED_TEXT]
    assert runtime.cancel_calls == [
        (console.resolve_response.agent_id, console.resolve_response.platform_user_id)
    ]


async def test_stop_without_active_run(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient()
    runtime.cancel_error = AppError(ErrorCode.NO_ACTIVE_RUN)
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/stop"))

    assert _sent_texts(adapter) == ["当前没有执行中的任务"]


async def test_new_command_creates_conversation(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/new"))

    assert _sent_texts(adapter) == [NEW_CONVERSATION_TEXT]
    assert runtime.conversations == [
        (console.resolve_response.agent_id, console.resolve_response.platform_user_id)
    ]


async def test_skills_failure_replies_unavailable(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    console.skills_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/skills"))

    assert _sent_texts(adapter) == [SKILLS_UNAVAILABLE_TEXT]


async def test_skills_success_lists_effective_catalog(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    console.skills = [
        {
            "skill_id": str(uuid4()),
            "key": "policy-check",
            "name": "policy-check",
            "platform_label": "设备策略检查",
            "description": "检查客户设备策略",
        }
    ]
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/skills"))

    assert _sent_texts(adapter) == ["设备策略检查: 检查客户设备策略"]


async def test_skills_empty_catalog_reply(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient()
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope(text="/skills"))

    assert _sent_texts(adapter) == [NO_SKILLS_TEXT]


async def test_channel_adapter_failure_does_not_crash(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(
        [
            SseEvent(type="run.created", data={"run_id": "run-1"}),
            SseEvent(type="message.delta", data={"delta": "x"}),
            SseEvent(type="run.completed", data={"status": "COMPLETED", "final_text": "x"}),
        ]
    )
    adapter = _BrokenSendAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)

    await pipeline.handle(adapter, make_envelope())
    await pipeline.handle(adapter, make_envelope(text="/bind CODE"))

    assert len(runtime.run_requests) == 1


async def test_consume_loop_survives_handler_crash(catalog: MessageCatalog) -> None:
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient([])
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(console, runtime, catalog=catalog)
    original_resolve = console.resolve
    calls = {"count": 0}

    async def flaky_resolve(
        request: ChannelResolveRequest,
        tenant_id: str,
    ) -> ChannelResolveResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return await original_resolve(request, tenant_id)

    console.resolve = flaky_resolve  # type: ignore[method-assign]
    consume_task = asyncio.create_task(pipeline.consume(adapter))
    try:
        await adapter.push(make_envelope("第一条", message_id="m-1"))
        await adapter.push(make_envelope("第二条", message_id="m-2"))
        for _ in range(100):
            if runtime.run_requests:
                break
            await asyncio.sleep(0.01)
    finally:
        consume_task.cancel()
        with suppress(asyncio.CancelledError):
            await consume_task

    assert len(runtime.run_requests) == 1
    assert runtime.run_requests[0].message.id == "m-2"
