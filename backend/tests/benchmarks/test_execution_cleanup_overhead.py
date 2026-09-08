"""执行清理开销基准（TASK-018 / B-LIFE-01）。

B-R06（test_runtime_overhead.py）已覆盖纯框架 P95/P99；
本文件覆盖重复断连：不积累 active execution、正常请求仍成功、预算保持。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def _ids(char: str, index: int) -> tuple[str, str, str]:
    suffix = f"{index:032x}"
    return (f"req_{suffix}", f"trace_{suffix}", f"exec_{suffix}")


async def _service() -> tuple[RuntimeApplicationService, PostgreSQLRegistryStore]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service, store


@pytest.mark.asyncio
async def test_B_LIFE_01_repeated_disconnects_do_not_accumulate() -> None:
    """B-LIFE-01：重复真实断连不积累 active execution；正常请求仍成功；预算保持。"""
    from fluxion.services.runtime_utils import DevEchoModelProvider

    service, store = await _service()
    real_complete = DevEchoModelProvider.complete

    async def slow_complete(self, request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)
        return await real_complete(self, request)

    DevEchoModelProvider.complete = slow_complete  # type: ignore[method-assign]
    try:
        for index in range(5):
            request_id, trace_id, execution_id = _ids("d", index)
            stream = service.stream(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    agent_definition_id="assistant",
                    session_id=f"session-disc-{index}",
                    input_message="hello",
                    request_id=request_id,
                    trace_id=trace_id,
                    execution_id=execution_id,
                )
            )

            async def _drain(
                events: Any = stream,  # type: ignore[assignment]
            ) -> list:
                return [event async for event in events]

            consumer = asyncio.create_task(_drain())
            await asyncio.sleep(0.5)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            record = await service.trace_store.get(trace_id)
            assert record is not None
    finally:
        DevEchoModelProvider.complete = real_complete  # type: ignore[method-assign]

    # 不积累：本地执行字典为空；正常请求仍成功且预算内（n=30 稳定 p95）。
    assert service._runtime.memory._l0 == {}
    samples: list[float] = []
    try:
        for index in range(30):
            request_id, trace_id, execution_id = _ids("e", index)
            started = time.perf_counter()
            result = await service.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    agent_definition_id="assistant",
                    session_id="session-ok",
                    input_message="hello",
                    request_id=request_id,
                    trace_id=trace_id,
                    execution_id=execution_id,
                )
            )
            samples.append((time.perf_counter() - started) * 1000)
            assert result.output == "dev: hello"
    finally:
        await service.close()
        await store.close()
    samples.sort()
    assert samples[int(len(samples) * 0.95)] <= 50.0
