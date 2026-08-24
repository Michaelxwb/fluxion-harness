from __future__ import annotations

import asyncio
from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from tests.channel_helpers import RecordingRuntime

from fluxion.plugins.channel_adapters import WebChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.channel_app import ChannelApplicationService


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_C106_bind_p95_under_300ms_and_chat_p95_under_200ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    runtime = RecordingRuntime()
    counter = 0

    def code_factory() -> str:
        nonlocal counter
        counter += 1
        return f"CODE-{counter}"

    service = ChannelApplicationService(store, runtime, code_factory=code_factory)
    adapter = WebChannelAdapter()
    loop.run_until_complete(store.initialize())
    bind_ms: list[float] = []
    chat_ms: list[float] = []
    run_index = 0

    def run_once() -> object:
        nonlocal run_index
        run_index += 1
        user_id = f"user-{run_index}"
        channel_user_id = f"browser-{run_index}"
        loop.run_until_complete(service.create_platform_user("tenant-a", user_id))
        issued = loop.run_until_complete(service.issue_bind_code("tenant-a", user_id))
        started = perf_counter_ns()
        loop.run_until_complete(service.handle(adapter, _message(channel_user_id, f"/bind {issued.code}")))
        bind_ms.append((perf_counter_ns() - started) / 1_000_000)
        started = perf_counter_ns()
        result = loop.run_until_complete(service.handle(adapter, _message(channel_user_id, "ping")))
        chat_ms.append((perf_counter_ns() - started) / 1_000_000)
        return result

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=100)
        assert quantiles(bind_ms, n=20, method="inclusive")[18] <= 300.0
        assert quantiles(chat_ms, n=20, method="inclusive")[18] <= 200.0
    finally:
        loop.run_until_complete(store.close())
        loop.close()


def _message(channel_user_id: str, content: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conversation-{channel_user_id}",
        message_id=f"message-{channel_user_id}-{content}",
        content=content,
        runtime_profile_id="assistant",
    )
