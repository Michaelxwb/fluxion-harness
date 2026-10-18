"""PG 服务端时钟：Task 租约与绝对期限裁决的唯一时间权威（design §3.5 时间）。

进程墙钟（`datetime.now(UTC)`）只允许用于计时、退避与展示，**不得**用于判定业务期限或
租约归属：本机时钟偏差会把"钟慢几分钟"变成"执行权多几分钟"。
`database_now` 向 PG 取一次服务端时间（`statement_timestamp()`，语句起点）：与租约/期限列
同一时钟域；在行锁之后调用得到的即**锁后**时刻（fencing 与判定共用同一口径）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def database_now(session: AsyncSession) -> datetime:
    """当前 PG 服务端时间；取不到即显式失败（绝不回落到本机墙钟）。"""
    now = await session.scalar(select(func.statement_timestamp()))
    if not isinstance(now, datetime):
        raise RuntimeError("database clock missing")
    return now
