from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from tests.channel_helpers import RecordingRuntime

from fluxion.registry import ChatAccessRecord, SQLiteRegistryStore
from fluxion.runtime.mcp import MCPHTTPClientPool, MCPHTTPPoolKey
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_P13_01_runtime_framework_p95_p99(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    service = loop.run_until_complete(_runtime_service())
    samples: list[float] = []
    index = 0

    def run_once() -> object:
        nonlocal index
        index += 1
        started = perf_counter_ns()
        result = loop.run_until_complete(
            service.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    session_id=f"benchmark-{index}",
                    input_message="ping",
                )
            )
        )
        samples.append(_elapsed_ms(started))
        return result

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=200)
        assert _percentile(samples, 95) <= 50.0
        assert _percentile(samples, 99) <= 100.0
    finally:
        loop.run_until_complete(service.close())
        loop.close()


def test_B_P13_01_chat_pre_model_p95_under_200ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ChannelApplicationService(store, RecordingRuntime())
    token = "benchmark-access-token"
    loop.run_until_complete(store.initialize())
    loop.run_until_complete(store.create_chat_access(_chat_access(token)))
    samples: list[float] = []
    index = 0

    def run_once() -> object:
        nonlocal index
        index += 1
        started = perf_counter_ns()
        result = loop.run_until_complete(
            service.handle_chat_access(
                token,
                conversation_id=f"conversation-{index}",
                content="ping",
                request_id=f"request-{index}",
                trace_id=f"trace-{index}",
            )
        )
        samples.append(_elapsed_ms(started))
        return result

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=200)
        assert _percentile(samples, 95) <= 200.0
    finally:
        loop.run_until_complete(store.close())
        loop.close()


def test_B_P13_01_mcp_pool_hit_p95_under_10ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    pool = MCPHTTPClientPool(ttl_seconds=60, max_clients=4)
    key = MCPHTTPPoolKey("tenant-a", "user-a", "http://mcp", "v1", "1")
    loop.run_until_complete(
        pool.get_client(key, headers={}, timeout_ms=1_000, credential_ref=None)
    )
    samples: list[float] = []

    def run_once() -> object:
        started = perf_counter_ns()
        client = loop.run_until_complete(
            pool.get_client(key, headers={}, timeout_ms=1_000, credential_ref=None)
        )
        samples.append(_elapsed_ms(started))
        return client

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=500)
        assert pool.hit_count >= 500
        assert _percentile(samples, 95) <= 10.0
    finally:
        loop.run_until_complete(pool.close())
        loop.close()


async def _runtime_service() -> RuntimeApplicationService:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=1_000,
        )
    )
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
        )
    )
    return service


def _chat_access(token: str) -> ChatAccessRecord:
    return ChatAccessRecord(
        access_id="benchmark-access",
        tenant_id="tenant-a",
        platform_user_id="user-a",
        runtime_profile_id="assistant",
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_at=datetime.now(UTC),
    )


def _elapsed_ms(started_ns: int) -> float:
    return (perf_counter_ns() - started_ns) / 1_000_000


def _percentile(samples: list[float], percentile: int) -> float:
    return quantiles(samples, n=100, method="inclusive")[percentile - 1]
