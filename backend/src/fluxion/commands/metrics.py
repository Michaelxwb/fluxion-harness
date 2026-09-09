"""Command Plane 进程内指标（设计 §22.2）。

最小实现、无外部依赖：计数器 + 延迟分布（count/sum/max），单事件循环内
操作（无锁；多循环共享实例需外部同步——当前 composition 均为单实例持有）。

指标命名对齐设计：`fluxion_command_total{command,result}`、
`fluxion_command_latency_ms{command}` 等在 snapshot() 的 key 结构中体现。
Prometheus exposition（/metrics）在本任务范围外，后续按需接入。
"""

from __future__ import annotations


class CommandMetrics:
    """命令指标注册表（进程内；测试可断言，生产 exposition 后续接入）。"""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], int] = {}
        self._latency_count: dict[str, int] = {}
        self._latency_sum_ms: dict[str, float] = {}
        self._latency_max_ms: dict[str, float] = {}

    def observe(self, command: str, result: str, latency_ms: float) -> None:
        """记录一次命令执行结果（result 即命令返回 code）。"""
        key = (command, result)
        self._counters[key] = self._counters.get(key, 0) + 1
        self._latency_count[command] = self._latency_count.get(command, 0) + 1
        self._latency_sum_ms[command] = self._latency_sum_ms.get(command, 0.0) + latency_ms
        self._latency_max_ms[command] = max(
            self._latency_max_ms.get(command, 0.0), latency_ms
        )

    def snapshot(self) -> dict[str, object]:
        """测试/调试用快照：counters 以 (command, result) 为键。"""
        average_ms = {
            command: self._latency_sum_ms[command] / count
            for command, count in self._latency_count.items()
            if count
        }
        return {
            "counters": dict(self._counters),
            "latency_count": dict(self._latency_count),
            "latency_avg_ms": average_ms,
            "latency_max_ms": dict(self._latency_max_ms),
        }
