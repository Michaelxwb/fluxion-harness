from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from fakes import FakeConsoleClient
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotItem, BotSnapshotResponse
from muad_im_gateway.application.bot_snapshot import BotSnapshotCache


async def test_snapshot_starts_empty() -> None:
    cache = BotSnapshotCache(FakeConsoleClient(), tenant_id="t1")
    assert cache.is_ready() is False
    assert cache.revision is None
    assert cache.items == ()
    assert cache.console_reachable is False


async def test_refresh_populates_revision_and_items() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-9")
    cache = BotSnapshotCache(console, tenant_id="t1")

    await cache.refresh()

    assert cache.is_ready() is True
    assert cache.revision == "rev-9"
    assert cache.console_reachable is True
    assert console.bots_calls == 1


async def test_refresh_failure_keeps_previous_revision() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    cache = BotSnapshotCache(console, tenant_id="t1")
    await cache.refresh()

    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    await cache.refresh()

    assert cache.revision == "rev-1"
    assert cache.is_ready() is True
    assert cache.console_reachable is False


async def test_run_forever_polls_until_cancelled() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    cache = BotSnapshotCache(console, tenant_id="t1", interval_sec=0.01)

    task = asyncio.create_task(cache.run_forever())
    try:
        for _ in range(100):
            if console.bots_calls >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert console.bots_calls >= 2
    assert cache.revision == "rev-1"


def make_item(secret: str = "wecom-bot-1") -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id="bot-1",
        secret=secret,
        agent_id=uuid4(),
    )


async def test_revision_change_invokes_snapshot_callback() -> None:
    item = make_item()
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="r1", items=[item])
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    await cache.refresh()
    assert calls == [(item,)]

    await cache.refresh()
    assert len(calls) == 1

    console.bot_snapshot = BotSnapshotResponse(revision="r2", items=[item])
    await cache.refresh()
    assert calls == [(item,), (item,)]


async def test_failed_refresh_does_not_invoke_snapshot_callback() -> None:
    console = FakeConsoleClient()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    calls: list[tuple[BotSnapshotItem, ...]] = []

    async def on_change(items: tuple[BotSnapshotItem, ...]) -> None:
        calls.append(items)

    cache = BotSnapshotCache(console, tenant_id="t1", on_snapshot_changed=on_change)
    await cache.refresh()

    assert calls == []
    assert cache.console_reachable is False
