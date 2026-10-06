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
            # 不完整/超容量的读取：丢弃本次，保留旧快照到下一节拍重拉
            self._console_reachable = True
            logger.warning("bot_snapshot_incomplete_discarded revision=%s", self._revision)
            return
        revision, items = collected
        self._console_reachable = True
        if revision == self._revision:
            return
        # **先应用、成功了才算这个 revision 生效**（2026-10-06 修）。此前是先提交 `_revision`
        # 再 `await` 回调：回调一抛错，新 revision 已经被记成"完成"，下一轮同 revision 直接
        # 跳过 ⇒ 连接管理器永远停在旧配置（实测：回调只被调用一次，`is_ready()` 却是 True）。
        # 也**不能**让异常冒出去：那样 `run_forever` 的下一拍会重来、但启动期的 `refresh` 会
        # 把整个进程带下去。这里记 ERROR 并保留待应用状态，下一轮重试。
        if self._on_snapshot_changed is not None:
            try:
                await self._on_snapshot_changed(items)
            except Exception:
                logger.exception("bot_snapshot_apply_failed revision=%s", revision)
                return
        self._revision = revision
        self._items = items

    async def _collect_snapshot(self) -> tuple[str, tuple[BotSnapshotItem, ...]] | None:
        """按 total 有界收齐同一 revision 的所有页；**收不齐就返回 None（不发布）**。

        "不完整不发布"是硬要求：`apply_snapshot` 的语义是"这份就是全部"，少读了谁，谁就会被
        当成已删除而**断开连接**。所以两条不完整路径都要拦住：① 页数超过有界读取能力
        （截断）；② 实际收齐的条目数与声明的 `total` 对不上。
        """
        first = await self._console.bots(
            self._tenant_id, page=1, page_size=BOT_SNAPSHOT_PAGE_SIZE
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
                self._tenant_id, page=page, page_size=page_size
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
