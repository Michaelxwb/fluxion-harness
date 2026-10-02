"""[每轮独立数据存储] Redis 号位的占用语义：并发运行不得共用同一个号。

回归对象：号位原先按 `os.getpid() % 6` 取 —— 两个并发运行只要 pid 同余就落在同一个 Redis DB
（1/6 概率）。Redis 承载去重键与队列，共用即隔离失效、退化成「单跑绿、串跑红」，正是独立数据
存储要消除的那类症状。现改为 `SET NX EX` 原子占位 + 收尾释放。

本文件只用 Redis，不需要 PostgreSQL；释放走公开入口 `drop_datastore`（`DROP DATABASE IF
EXISTS` 对不存在的库是空操作）。
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from muad_common import SharedSettings

from tests.acceptance.datastores import drop_datastore, isolated_redis_url


def _db_index(url: str) -> str:
    """取 Redis URL 里的号（路径段），如 `redis://host:6379/10` → `10`。"""
    return urlsplit(url).path.lstrip("/")


def _owner() -> str:
    return f"test-claim-{uuid.uuid4().hex[:8]}"


def test_two_concurrent_owners_never_share_a_redis_db() -> None:
    """两次取号必须落在不同 DB —— 按 pid 取模时这里会失败（同余即同号）。"""
    base = SharedSettings().require_redis_url()
    first, second = _owner(), _owner()
    try:
        assert _db_index(isolated_redis_url(base, first)) != _db_index(
            isolated_redis_url(base, second)
        )
    finally:
        drop_datastore(first, redis_url=base)
        drop_datastore(second, redis_url=base)


def test_released_slot_can_be_claimed_again() -> None:
    """收尾释放后，同一个号必须能被下一个运行拿走（否则并发额度会随异常静默缩水）。"""
    base = SharedSettings().require_redis_url()
    holder, next_owner = _owner(), _owner()
    try:
        held = _db_index(isolated_redis_url(base, holder))
        drop_datastore(holder, redis_url=base)
        assert _db_index(isolated_redis_url(base, next_owner)) == held
    finally:
        drop_datastore(holder, redis_url=base)
        drop_datastore(next_owner, redis_url=base)
