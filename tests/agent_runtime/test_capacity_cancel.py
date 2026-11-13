"""Cancellation while waiting for model capacity must stop the wait promptly."""

from __future__ import annotations

import asyncio

import pytest
from muad_agent_core.agent import RunnerCancelled
from muad_agent_runtime.application.executor import PooledModelProvider, _Emitted

pytestmark = pytest.mark.unit


async def test_cancelled_provider_stops_before_capacity_retry() -> None:
    provider = object.__new__(PooledModelProvider)
    event = asyncio.Event()
    event.set()
    provider._cancel_event = event  # noqa: SLF001 - focused guard for the wait loop

    with pytest.raises(RunnerCancelled):
        await provider._allocate((), 0)  # noqa: SLF001 - no allocator/DB touched


async def test_cancelled_pool_call_stops_its_inflight_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = object.__new__(PooledModelProvider)
    started, closed = asyncio.Event(), asyncio.Event()

    async def blocked_call(*args: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(provider, "_call", blocked_call)
    task = asyncio.create_task(provider._call_until_lost(  # noqa: SLF001
        None, None, None, _Emitted(), asyncio.Event()
    ))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set(), "cancel must wait for the physical provider call to stop"
