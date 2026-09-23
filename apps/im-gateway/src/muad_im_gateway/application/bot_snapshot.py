from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from math import ceil

from muad_api import AppError
from muad_contracts import DEFAULT_PAGE_SIZE, BotSnapshotItem

from .console_client import ConsoleClientPort

logger = logging.getLogger(__name__)

BOT_SNAPSHOT_POLL_SEC = 30.0
BOT_SNAPSHOT_PAGE_SIZE = DEFAULT_PAGE_SIZE
# 分页读取的上界：避免异常 total 造成无界翻页（超出即视为不一致，下个节拍重拉）
BOT_SNAPSHOT_MAX_PAGES = 100

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
            collected = await self._collect_snapshot()
        except AppError as exc:
            # 依赖故障保留最近一次完整快照，不清空
            self._console_reachable = False
            logger.warning("bot_snapshot_refresh_failed code=%s", exc.code)
            return
        if collected is None:
            # 跨页 revision/total 不一致：丢弃本次不完整读取，保留旧快照到下一节拍重拉
            self._console_reachable = True
            logger.warning("bot_snapshot_inconsistent_discarded revision=%s", self._revision)
            return
        revision, items = collected
        self._console_reachable = True
        changed = revision != self._revision
        self._revision = revision
        self._items = items
        if changed and self._on_snapshot_changed is not None:
            await self._on_snapshot_changed(self._items)

    async def _collect_snapshot(self) -> tuple[str, tuple[BotSnapshotItem, ...]] | None:
        """按 total 有界收齐同一 revision 的所有页；不一致返回 None（不发布）。"""
        first = await self._console.bots(
            self._tenant_id, page=1, page_size=BOT_SNAPSHOT_PAGE_SIZE
        )
        revision = first.revision
        total = first.total
        page_size = first.page_size or BOT_SNAPSHOT_PAGE_SIZE
        items = list(first.items)
        pages = min(max(ceil(total / page_size), 1), BOT_SNAPSHOT_MAX_PAGES)
        for page in range(2, pages + 1):
            nxt = await self._console.bots(
                self._tenant_id, page=page, page_size=page_size
            )
            if nxt.revision != revision or nxt.total != total:
                return None
            items.extend(nxt.items)
        return revision, tuple(items)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception("bot_snapshot_poll_failed")
            await asyncio.sleep(self._interval_sec)
