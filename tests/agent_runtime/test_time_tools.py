"""[TASK-009] `current_time` 工具：给 Agent 一个可信、带 IANA 时区的时间基准。

**为什么需要**：模型不知道"现在几点"，而 `create_schedule` 要判断 CRON/ONCE 与"下次何时"、
用户也常说"明天早上 9 点" —— 没有时间基准这些判断只能靠猜。
**为什么必须带 IANA 时区标识**：只有偏移量（`+08:00`）在跨夏令时/跨地区时不可靠，而调度侧的
时区口径就是 IANA 名（`harness-time#RULE-time-001`）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.time_tools import (
    CURRENT_TIME_TOOL,
    TimeToolSet,
    resolve_zone,
)

#: 注入的固定时刻（UTC 01:30 == 亚洲/上海 09:30）
FIXED_INSTANT = datetime(2026, 10, 2, 1, 30, 0, tzinfo=UTC)


def _registry_for(zone_name: str) -> ToolRegistry:
    registry = ToolRegistry()
    TimeToolSet(zone=ZoneInfo(zone_name), clock=lambda: FIXED_INSTANT).register(registry)
    return registry


async def _call(tool_set_registry: ToolRegistry, call_id: str = "call-1") -> str:
    definition = tool_set_registry.get(CURRENT_TIME_TOOL)
    assert definition.handler is not None, "current_time 必须注册处理器"
    return await definition.handler({}, call_id=call_id)


async def test_b08_current_time_returns_injected_instant_with_iana_zone() -> None:
    """B-08：注入已知固定时刻 → 返回该时刻，且带 IANA 时区标识（非裸 UTC 字符串）。

    真实边界：注入固定时钟；**不 mock 被测函数本身** —— 格式化与换算是真实执行的。
    """
    result = await _call(_registry_for("Asia/Shanghai"))

    # 固定时刻 + 真实换算：UTC 01:30 → 亚洲/上海 09:30
    assert result.startswith("2026-10-02T09:30:00"), f"未按注入时刻换算：{result}"
    assert "Asia/Shanghai" in result, f"必须带 IANA 时区标识，而不是只有偏移量：{result}"
    assert "+08:00" in result, f"偏移量应保留，便于模型与其它时刻比较：{result}"

    # 不得退化成裸 UTC 字符串
    assert "2026-10-02T01:30:00" not in result, f"返回了 UTC 时刻而非本地时刻：{result}"


async def test_b08_same_instant_renders_per_zone() -> None:
    """同一注入时刻在不同时区应给出不同本地表示 —— 证明时区真的参与换算。"""
    shanghai = await _call(_registry_for("Asia/Shanghai"))
    utc = await _call(_registry_for("UTC"))

    assert "T09:30:00" in shanghai
    assert "T01:30:00" in utc
    assert shanghai != utc


def test_b08_invalid_zone_is_rejected() -> None:
    """非法 IANA 时区必须显式报错，不得静默回退到某个默认时区。"""
    with pytest.raises(ValueError):
        resolve_zone("Not/AZone")
