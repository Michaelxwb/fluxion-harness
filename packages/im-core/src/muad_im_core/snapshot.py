"""入口快照缓存（Connector 与 Gateway 共用；渠道中立）。

**按 channel 过滤**是安全边界（设计 NFR-SEC-01）：Console 的 bot 快照里带 bot secret，
只有真正持有该通道长连接的运行时角色才该看到它。Gateway 请求 `WEBCHAT`（+ 自己的 webhook
通道），Connector 请求自己接管的通道；Gateway 因此从不接收企微 secret。

分页/不完整不发布语义与 `10-im-gateway` 一致：`apply_snapshot` 的"这份就是全部"按通道成立，
少读一个 bot 就会被当成删除而断连，所以收不齐就整批丢弃。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from math import ceil
from typing import Protocol

from muad_api import AppError
from muad_contracts import DEFAULT_PAGE_SIZE, BotSnapshotItem, BotSnapshotResponse

logger = logging.getLogger(__name__)

BOT_SNAPSHOT_POLL_SEC = 30.0
BOT_SNAPSHOT_PAGE_SIZE = DEFAULT_PAGE_SIZE
# 分页读取的上界：避免异常 total 造成无界翻页（超出即视为不一致，下个节拍重拉）
BOT_SNAPSHOT_MAX_PAGES = 100

SnapshotChangeCallback = Callable[[tuple[BotSnapshotItem, ...]], Awaitable[None]]


class BotSnapshotSource(Protocol):
    """快照读取口（Console 内部端点）。

    `channels` 是**本运行时角色**要的通道集合；`None` 表示不过滤（管理面/测试口径）。
    """

    async def bots(
        self,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        channels: frozenset[str] | None = None,
        include_secret: bool = True,
    ) -> BotSnapshotResponse: ...


class BotSnapshotCache:
    def __init__(
        self,
        console: BotSnapshotSource,
        *,
        tenant_id: str,
        channels: frozenset[str] | None = None,
        include_secret: bool = True,
        interval_sec: float = BOT_SNAPSHOT_POLL_SEC,
        on_snapshot_changed: SnapshotChangeCallback | None = None,
    ) -> None:
        self._console = console
        self._tenant_id = tenant_id
        self._channels = frozenset(channels) if channels is not None else None
        self._include_secret = include_secret
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
    def channels(self) -> frozenset[str] | None:
        return self._channels

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
            # 不完整/超容量的读取：丢弃本次，保留旧快照到下一节拍重拉
            self._console_reachable = True
            logger.warning("bot_snapshot_incomplete_discarded revision=%s", self._revision)
            return
        revision, items = collected
        self._console_reachable = True
        if revision == self._revision:
            return
        # **先应用、成功了才算这个 revision 生效**（2026-10-06 修）：回调抛错时不能先记
        # revision，否则连接管理器永远停在旧配置。异常不外抛：启动期 refresh 会把进程带下去。
        if self._on_snapshot_changed is not None:
            try:
                await self._on_snapshot_changed(items)
            except Exception:
                logger.exception("bot_snapshot_apply_failed revision=%s", revision)
                return
        self._revision = revision
        self._items = items

    async def _collect_snapshot(self) -> tuple[str, tuple[BotSnapshotItem, ...]] | None:
        """按 total 有界收齐同一 revision 的所有页；**收不齐就返回 None（不发布）**。"""
        first = await self._console.bots(
            self._tenant_id,
            page=1,
            page_size=BOT_SNAPSHOT_PAGE_SIZE,
            channels=self._channels,
            include_secret=self._include_secret,
        )
        revision = first.revision
        total = first.total
        page_size = first.page_size or BOT_SNAPSHOT_PAGE_SIZE
        pages = ceil(total / page_size)
        if pages > BOT_SNAPSHOT_MAX_PAGES:
            logger.warning(
                "bot_snapshot_over_capacity total=%s pages=%s cap=%s",
                total,
                pages,
                BOT_SNAPSHOT_MAX_PAGES,
            )
            return None
        items = list(first.items)
        for page in range(2, pages + 1):
            nxt = await self._console.bots(
                self._tenant_id,
                page=page,
                page_size=page_size,
                channels=self._channels,
                include_secret=self._include_secret,
            )
            if nxt.revision != revision or nxt.total != total:
                return None
            items.extend(nxt.items)
        if len(items) != total:
            logger.warning("bot_snapshot_incomplete total=%s collected=%s", total, len(items))
            return None
        return revision, tuple(items)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception("bot_snapshot_poll_failed")
            await asyncio.sleep(self._interval_sec)
