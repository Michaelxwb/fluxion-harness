"""Cancellation while waiting for model capacity must stop the wait promptly."""

from __future__ import annotations

import asyncio

import pytest
from muad_agent_core.agent import RunnerCancelled
from muad_agent_runtime.application.executor import PooledModelProvider

pytestmark = pytest.mark.unit


async def test_cancelled_provider_stops_before_capacity_retry() -> None:
    provider = object.__new__(PooledModelProvider)
    event = asyncio.Event()
    event.set()
    provider._cancel_event = event  # noqa: SLF001 - focused guard for the wait loop

    with pytest.raises(RunnerCancelled):
        await provider._allocate((), 0)  # noqa: SLF001 - no allocator/DB touched
