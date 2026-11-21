"""Connector 入站循环：命令/文本都走真实的批次入口，坏消息不能停掉循环。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from muad_im_connector.channels.wecom.sdk_port import WeComInboundMessage
from muad_im_connector.runtime import ConnectorRuntime
from muad_im_core.inbound_batch import BatchSettings
from muad_im_core.ingress import PublishOutcome

from tests.im_core.test_inbound_batch import MemoryJournal as _MemoryJournal

pytestmark = pytest.mark.unit

TENANT = "tenant"
BOT = "bot"




class _Replies:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.routes: list[dict[str, Any]] = []

    async def remember_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)

    async def remember_route(self, **kwargs: Any) -> None:
        self.routes.append(kwargs)


class _Publisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(
        self,
        event: Any,
        *,
        dedupe_id: str | None = None,
        dedupe_ids: Any = None,
    ) -> PublishOutcome:
        self.events.append(event)
        return PublishOutcome.PUBLISHED


class _PlatformSettings:
    async def batch(self, tenant_id: str) -> BatchSettings:
        return BatchSettings(quiet_ms=1, max_wait_ms=20, max_attachments=5)


class _Adapter:
    name = "WECOM"


def _runtime(
    *, batch_journal: _MemoryJournal | None = None,
    attachment_concurrency: int = 8, inbound_queue_size: int = 10_000,
) -> tuple[ConnectorRuntime, _Replies, _Publisher]:
    replies = _Replies()
    publisher = _Publisher()
    runtime = ConnectorRuntime(
        tenant_id=TENANT,
        handled_channels=frozenset({"WECOM"}),
        adapter=_Adapter(),  # type: ignore[arg-type]
        leases=object(),  # type: ignore[arg-type]
        replies=replies,  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        outbound=object(),  # type: ignore[arg-type]
        snapshot=object(),  # type: ignore[arg-type]
        renew_sec=1.0,
        platform_settings=_PlatformSettings(),  # type: ignore[arg-type]
        batch_journal=batch_journal,  # type: ignore[arg-type]
        attachment_concurrency=attachment_concurrency,
        inbound_queue_size=inbound_queue_size,
    )
    runtime._owned[(TENANT, BOT)] = SimpleNamespace(lease=SimpleNamespace(epoch=1))
    return runtime, replies, publisher


def _message(message_id: str, text: str) -> WeComInboundMessage:
    return WeComInboundMessage(
        bot_id=BOT,
        message_id=message_id,
        external_user_id="user",
        external_conversation_id=None,
        message_type="text",
        text=text,
        reply_id=f"reply-{message_id}",
    )


async def test_command_message_publishes_without_a_coroutine_frame() -> None:
    runtime, replies, publisher = _runtime(batch_journal=_MemoryJournal())

    await runtime._publish_inbound(_message("command-1", "/new"))

    assert [event.external_message_id for event in publisher.events] == ["command-1"]
    assert replies.messages[-1]["message_id"] == "command-1"


async def test_text_message_is_batched_then_published() -> None:
    runtime, _replies, publisher = _runtime(batch_journal=_MemoryJournal())

    await runtime._publish_inbound(_message("text-1", "你好"))
    await asyncio.sleep(0.05)

    assert [event.external_message_id for event in publisher.events] == ["text-1"]


async def test_inbound_loop_keeps_consuming_after_one_bad_message() -> None:
    runtime, _replies, _publisher = _runtime()
    handled: list[str] = []

    async def flaky(message: WeComInboundMessage) -> None:
        handled.append(message.message_id)
        if len(handled) == 1:
            raise RuntimeError("boom")

    runtime._publish_inbound = flaky  # type: ignore[method-assign]
    task = asyncio.create_task(runtime._inbound_loop())
    runtime.raw_sink(_message("bad-1", "first"))
    runtime.raw_sink(_message("good-1", "second"))

    await asyncio.sleep(0.05)

    assert handled == ["bad-1", "good-1"]
    runtime._stopped.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
