"""B-110: Gateway /bind 命令与稳定幂等键（真实 Console bind HTTP → PostgreSQL）。

不得 Mock 的真实边界：真实 Console 服务进程（真实 socket）+ 真实 PostgreSQL
（bind_code 状态、channel_identity）+ 真实 HTTP 幂等键（Idempotency-Key=原 message_id）。
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fakes import ConsoleProcess, FakeRuntimeClient, make_envelope
from muad_api.catalog import MessageCatalog
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import BindCode, ChannelIdentity
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import (
    BIND_SUCCESS_TEXT,
    BIND_USAGE_TEXT,
    InboundPipeline,
)
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore
from sqlalchemy import func, select

# tests/ 需在 sys.path 上才能复用 console_channel 的真实 Console 夹具
_TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

from console_channel.conftest import ChannelContext, channel, database_guard  # noqa: E402,F401


@pytest.fixture()
async def console_server(tmp_path: Path) -> AsyncIterator[ConsoleProcess]:
    server = ConsoleProcess()
    server.start(tmp_path)
    try:
        yield server
    finally:
        server.stop()


def _pipeline(
    *,
    console: ConsoleClient,
    runtime: FakeRuntimeClient,
    catalog: MessageCatalog,
    channel_ctx: ChannelContext,
) -> tuple[InboundPipeline, FakeChannelAdapter]:
    adapter = FakeChannelAdapter()
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=channel_ctx.tenant_id,
        locale="zh-CN",
    )
    return pipeline, adapter


def _envelope(channel_ctx: ChannelContext, *, text: str, message_id: str):
    return make_envelope(
        text=text,
        message_id=message_id,
        external_user_id=channel_ctx.unbound_external_user_id,
    ).model_copy(update={"bot_id": channel_ctx.bot_id})


def _sent_texts(adapter: FakeChannelAdapter) -> list[str]:
    return [message.text for _route, message in adapter.sent]


async def _count_identities(channel_ctx: ChannelContext, external_user_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ChannelIdentity)
                .where(
                    ChannelIdentity.tenant_id == channel_ctx.tenant_id,
                    ChannelIdentity.external_user_id == external_user_id,
                )
            )
            or 0
        )


async def _fetch_code(channel_ctx: ChannelContext) -> BindCode | None:
    from muad_console_platform.application.channel_service import hash_bind_code

    async with get_session_factory()() as session:
        return await session.scalar(
            select(BindCode).where(
                BindCode.tenant_id == channel_ctx.tenant_id,
                BindCode.code_hash == hash_bind_code(channel_ctx.valid_bind_code),
            )
        )


async def test_b110_bind_success_replies_and_persists_identity(
    console_server: ConsoleProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = FakeRuntimeClient()
    console = ConsoleClient(console_server.url)
    pipeline, adapter = _pipeline(
        console=console, runtime=runtime, catalog=catalog, channel_ctx=channel
    )
    message_id = f"msg-bind-{time.time_ns()}"
    try:
        with caplog.at_level(logging.DEBUG):
            await pipeline.handle(
                adapter,
                _envelope(channel, text=f"/bind {channel.valid_bind_code}", message_id=message_id),
            )

        assert _sent_texts(adapter) == [BIND_SUCCESS_TEXT]
        assert await _count_identities(channel, channel.unbound_external_user_id) == 1
        code = await _fetch_code(channel)
        assert code is not None and code.status == "USED"
        assert runtime.run_requests == []  # /bind 不进 LLM、不建 Run
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert channel.valid_bind_code not in logged  # 绑定码不进日志
    finally:
        await console.aclose()


async def test_b110_replayed_message_does_not_consume_twice(
    console_server: ConsoleProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    runtime = FakeRuntimeClient()
    console = ConsoleClient(console_server.url)
    pipeline, adapter = _pipeline(
        console=console, runtime=runtime, catalog=catalog, channel_ctx=channel
    )
    message_id = f"msg-bind-replay-{time.time_ns()}"
    envelope = _envelope(
        channel, text=f"/bind {channel.valid_bind_code}", message_id=message_id
    )
    try:
        await pipeline.handle(adapter, envelope)
        used_at_first = (await _fetch_code(channel)).used_at
        await pipeline.handle(adapter, envelope)  # 同 message_id 重投：幂等重放
        assert _sent_texts(adapter) == [BIND_SUCCESS_TEXT, BIND_SUCCESS_TEXT]
        assert await _count_identities(channel, channel.unbound_external_user_id) == 1
        code = await _fetch_code(channel)
        assert code is not None and code.used_at == used_at_first  # 未二次消费
    finally:
        await console.aclose()


async def test_b110_error_branches_use_catalog_texts(
    console_server: ConsoleProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    runtime = FakeRuntimeClient()
    console = ConsoleClient(console_server.url)
    pipeline, adapter = _pipeline(
        console=console, runtime=runtime, catalog=catalog, channel_ctx=channel
    )
    try:
        # 缺参数：本地提示，不发请求
        await pipeline.handle(
            adapter,
            _envelope(channel, text="/bind", message_id=f"msg-usage-{time.time_ns()}"),
        )
        assert _sent_texts(adapter) == [BIND_USAGE_TEXT]

        # 未知码
        adapter2 = FakeChannelAdapter()
        await pipeline.handle(
            adapter2,
            _envelope(
                channel,
                text=f"/bind {channel.unknown_bind_code}",
                message_id=f"msg-unknown-{time.time_ns()}",
            ),
        )
        assert _sent_texts(adapter2) == [catalog.message("BIND_CODE_INVALID", "zh-CN")]

        # 过期码
        adapter3 = FakeChannelAdapter()
        await pipeline.handle(
            adapter3,
            _envelope(
                channel,
                text=f"/bind {channel.expired_bind_code}",
                message_id=f"msg-expired-{time.time_ns()}",
            ),
        )
        assert _sent_texts(adapter3) == [catalog.message("BIND_CODE_EXPIRED", "zh-CN")]

        # 已用码（不同 message_id）：单次规则拒绝
        adapter4 = FakeChannelAdapter()
        await pipeline.handle(
            adapter4,
            _envelope(
                channel,
                text=f"/bind {channel.used_bind_code}",
                message_id=f"msg-used-{time.time_ns()}",
            ),
        )
        assert _sent_texts(adapter4) == [catalog.message("BIND_CODE_INVALID", "zh-CN")]

        assert runtime.run_requests == []
    finally:
        await console.aclose()
