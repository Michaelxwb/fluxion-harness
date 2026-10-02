"""`current_time` 工具：给 Agent 一个可信、带 IANA 时区的时间基准。

**为什么需要**：模型不知道"现在几点"，而 `create_schedule` 要判断 CRON/ONCE 与"下次何时"、
用户也常说"明天早上 9 点" —— 没有时间基准，这些判断只能靠猜。

**为什么必须带 IANA 时区标识**：只有偏移量（`+08:00`）在跨夏令时/跨地区时不可靠；而调度侧的
时区口径本来就是 IANA 名（`harness-time#RULE-time-001`：`timezone` 必填且为合法 IANA 时区）。
因此返回值同时给「带偏移量的 ISO 时刻」与「IANA 时区名」，模型可以直接用它做相对时间推算。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry

CURRENT_TIME_TOOL = "current_time"

CURRENT_TIME_DESCRIPTION = (
    "Get the current date and time, including the IANA time zone name. Call this before any "
    "reasoning about 'now', relative dates ('tomorrow 9am') or scheduling, instead of guessing."
)

#: 时钟可注入：测试用固定时刻断言返回值，生产用系统时钟。
#: **注入的是时钟本身，不是被测函数** —— 换算与格式化仍走真实代码路径。
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


def resolve_zone(name: str) -> ZoneInfo:
    """把 IANA 时区名解析成 `ZoneInfo`；非法名**显式报错**，不静默回退默认时区。

    与调度侧同口径（`harness-time#RULE-time-001`：非法 IANA 即 `ValueError`）——静默回退会让
    一个配错的时区在很长时间里表现为"时间就是不对"，且没有任何报错。
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid IANA time zone: {name}") from exc


class TimeToolSet:
    """注册 `current_time`。无参数、无副作用（`READ`）。"""

    def __init__(self, *, zone: ZoneInfo, clock: Clock = system_clock) -> None:
        self._zone = zone
        self._clock = clock

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name=CURRENT_TIME_TOOL,
                description=CURRENT_TIME_DESCRIPTION,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                effect=ToolEffect.READ,
                handler=self.current_time,
            )
        )

    async def current_time(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        now = self._clock().astimezone(self._zone)
        return f"{now.isoformat(timespec='seconds')} ({self._zone.key})"
