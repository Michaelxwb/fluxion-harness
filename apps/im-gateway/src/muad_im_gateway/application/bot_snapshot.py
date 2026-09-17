from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from muad_api import AppError
from muad_contracts import BotSnapshotItem

from .console_client import ConsoleClientPort

logger = logging.getLogger(__name__)

BOT_SNAPSHOT_POLL_SEC = 30.0

SnapshotChangeCallback = Callable[[tuple[BotSnapshotItem, ...]], Awaitable[None]]


class BotSnapshotCache:
    def __init__(
        self,
        console: ConsoleClientPort,
        *,
        tenant_id: str,
        interval_sec: float = BOT_SNAPSHOT_POLL_SEC,
        on_snapshot_changed: SnapshotChangeCallback | None = None,
    ) -> None:
        self._console = console
        self._tenant_id = tenant_id
        self._interval_sec = interval_sec
        self._on_snapshot_changed = on_snapshot_changed
        self._revision: str | None = None
        self._items: tuple[BotSnapshotItem, ...] = ()
        self._console_reachable = False

    @property
    def revision(self) -> str | None:
        return self._revision

    @property
    def items(self) -> tuple[BotSnapshotItem, ...]:
        return self._items

    @property
    def console_reachable(self) -> bool:
        return self._console_reachable

    def is_ready(self) -> bool:
        return self._revision is not None

    async def refresh(self) -> None:
        try:
            snapshot = await self._console.bots(self._tenant_id)
        except AppError as exc:
            self._console_reachable = False
            logger.warning("bot_snapshot_refresh_failed code=%s", exc.code)
            return
        self._console_reachable = True
        changed = snapshot.revision != self._revision
        self._revision = snapshot.revision
        self._items = tuple(snapshot.items)
        if changed and self._on_snapshot_changed is not None:
            await self._on_snapshot_changed(self._items)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception("bot_snapshot_poll_failed")
            await asyncio.sleep(self._interval_sec)
