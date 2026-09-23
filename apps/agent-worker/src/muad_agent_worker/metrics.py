"""进程内指标计数器（设计 §3.5 可观测性）。

V1 没有外部 metrics 后端：按指标名在进程内累加，并在每次累加时输出一条结构化
`metric` 日志（`metric/amount/total`），由统一 JSON 日志采集链路汇总；需要跨进程
聚合时由部署层按日志求和，不在此处引入新的基础设施。
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger("muad_agent_worker.metrics")

_counters: Counter[str] = Counter()


def increment(name: str, amount: int = 1) -> None:
    if amount <= 0:
        raise ValueError("metric increment must be positive")
    _counters[name] += amount
    logger.info(
        "metric",
        extra={"metric": name, "amount": amount, "total": _counters[name]},
    )


def value(name: str) -> int:
    return _counters[name]
