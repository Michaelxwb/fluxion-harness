"""容量验收指标采集（TASK-109；设计 §2.5 验收条件 / §3.7 配置契约 / §4.3 分层验收与报告）。

三条真实来源，全部不做替身：

1. **Prometheus 文本**：四单元真实 `GET /metrics`（api-kit 进程内注册表 → 真实 HTTP 文本），
   本模块只做严格解析与增量对账；坏行/非有限值/未闭合 label 一律显式失败，不静默回 0。
2. **PostgreSQL**：连接数、锁（含等待）、WAL 增长、库大小、事务/死锁/临时字节与
   `runtime.run_record` 的持久状态分布（受理/等待/执行并发）。查询一律参数化，租户谓词显式。
3. **分位数**：最近秩（nearest-rank）定义，可复算；空样本显式失败 —— 没有样本就不给数字。

所有对外结构都是 typed dataclass；错误消息不回带凭据/连接串原文。
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import asyncpg
import httpx

from tests.acceptance.capacity.stack import run_async

__all__ = [
    "METRICS_PATH",
    "OPEN_RUN_STATUSES",
    "TERMINAL_RUN_STATUSES",
    "LatencySummary",
    "MetricsError",
    "PostgresMetrics",
    "PostgresRate",
    "PostgresSnapshot",
    "StatementStat",
    "PrometheusSample",
    "RunState",
    "UnitMetricsSnapshot",
    "artifact_bytes",
    "metric_deltas",
    "parse_prometheus_text",
    "percentile",
    "scrape_unit_metrics",
    "summarize_latencies",
]

METRICS_PATH = "/metrics"
#: 非终态 Run 状态（与 `RunStatus` 的活跃集合同源；受理/等待/执行并发的分母）。
OPEN_RUN_STATUSES: tuple[str, ...] = ("CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT")
#: 终态 Run 状态（控制探测等待份额归还的判定集）。
TERMINAL_RUN_STATUSES: tuple[str, ...] = ("COMPLETED", "FAILED", "CANCELLED")

_SAMPLE_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?P<labels>\{(?:[^\"}]|\"(?:[^\"\\]|\\.)*\")*\})?"
    r"\s+(?P<value>\S+)"
    r"(?:\s+-?\d+)?$"
)
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n"}


class MetricsError(RuntimeError):
    """指标采集/解析失败：坏输入、空样本、不可达的 `/metrics` 或缺失的 SQL 结果。"""


# --------------------------------------------------------------------------------------
# Prometheus 文本：严格解析 + 增量对账
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrometheusSample:
    name: str
    labels: Mapping[str, str]
    value: float

    @property
    def key(self) -> str:
        """规范样本键：label 按名排序（与 Prometheus 文本输出的稳定序一致）。"""
        if not self.labels:
            return self.name
        rendered = ",".join(f'{name}="{value}"' for name, value in sorted(self.labels.items()))
        return f"{self.name}{{{rendered}}}"


@dataclass(frozen=True, slots=True)
class UnitMetricsSnapshot:
    """一个单元一次真实 `/metrics` 抓取；`samples` 保持文本顺序。"""

    unit: str
    base_url: str
    scraped_at: float
    samples: tuple[PrometheusSample, ...]

    def values(self) -> Mapping[str, float]:
        return {sample.key: sample.value for sample in self.samples}

    def value(self, key: str) -> float:
        """按规范键取值；序列不存在即显式失败（不伪造 0）。"""
        values = self.values()
        if key not in values:
            raise MetricsError(f"{self.unit} 的 /metrics 缺少样本 {key}")
        return values[key]


def _unescape(raw: str) -> str:
    result: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            if char not in _ESCAPES:
                raise MetricsError("Prometheus label 含未知转义序列")
            result.append(_ESCAPES[char])
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)
    if escaped:
        raise MetricsError("Prometheus label 以孤立反斜杠结尾")
    return "".join(result)


def _parse_labels(rendered: str | None, *, source: str, line_number: int) -> Mapping[str, str]:
    if rendered is None:
        return {}
    inner = rendered[1:-1]
    if not inner:
        return {}
    labels: dict[str, str] = {}
    position = 0
    for match in _LABEL.finditer(inner):
        if match.start() != position:
            raise MetricsError(f"{source}:{line_number} 的 label 段无法解析")
        name, raw_value = match.group(1), match.group(2)
        if name in labels:
            raise MetricsError(f"{source}:{line_number} 的 label {name} 重复")
        labels[name] = _unescape(raw_value)
        position = match.end()
        if position < len(inner):
            if inner[position] != ",":
                raise MetricsError(f"{source}:{line_number} 的 label 分隔符非法")
            position += 1
    if position != len(inner):
        raise MetricsError(f"{source}:{line_number} 的 label 段无法解析")
    return labels


def parse_prometheus_text(text: str, *, source: str) -> tuple[PrometheusSample, ...]:
    """严格解析 Prometheus 文本：注释/空行跳过，坏行、重复序列、非有限值显式失败。

    空文本是失败（真实 `/metrics` 始终声明目录）—— 不回"零样本"让调用方误以为指标为零。
    """
    if not text.strip():
        raise MetricsError(f"{source} 的 /metrics 文本为空：没有可用样本")
    samples: list[PrometheusSample] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_LINE.match(line)
        if match is None:
            raise MetricsError(f"{source}:{line_number} 不是合法样本行")
        try:
            value = float(match.group("value"))
        except ValueError as exc:
            raise MetricsError(f"{source}:{line_number} 的样本值不是数字") from exc
        if not math.isfinite(value):
            raise MetricsError(f"{source}:{line_number} 的样本值不是有限数")
        sample = PrometheusSample(
            name=match.group("name"),
            labels=_parse_labels(match.group("labels"), source=source, line_number=line_number),
            value=value,
        )
        if sample.key in seen:
            raise MetricsError(f"{source}:{line_number} 的样本 {sample.key} 重复出现")
        seen.add(sample.key)
        samples.append(sample)
    return tuple(samples)


def scrape_unit_metrics(
    client: httpx.Client, *, unit: str, base_url: str
) -> UnitMetricsSnapshot:
    """真实 HTTP 抓取一个单元的 `/metrics`；非 200/传输错误显式失败（不静默降级）。"""
    url = f"{base_url}{METRICS_PATH}"
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise MetricsError(f"{unit} 的 /metrics 抓取失败（{url}）：{type(exc).__name__}") from exc
    if response.status_code != 200:
        raise MetricsError(f"{unit} 的 /metrics 返回 {response.status_code}（{url}）")
    return UnitMetricsSnapshot(
        unit=unit,
        base_url=base_url,
        scraped_at=time.monotonic(),
        samples=parse_prometheus_text(response.text, source=url),
    )


def metric_deltas(before: UnitMetricsSnapshot, after: UnitMetricsSnapshot) -> Mapping[str, float]:
    """同一单元两次抓取的差值；某侧缺失的序列按计数器零值处理（序列出现前的取值就是 0）。

    两侧必须是同一单元：跨单元对账没有意义，直接拒绝。
    """
    if before.unit != after.unit:
        raise MetricsError(f"跨单元对账被拒：{before.unit} vs {after.unit}")
    before_values, after_values = before.values(), after.values()
    keys = set(before_values) | set(after_values)
    return {key: after_values.get(key, 0.0) - before_values.get(key, 0.0) for key in sorted(keys)}


# --------------------------------------------------------------------------------------
# 分位数：最近秩，可复算
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int
    p50: float
    p95: float
    p99: float
    maximum: float


def percentile(samples: Sequence[float], quantile: float) -> float:
    """最近秩分位数：`rank = ceil(q × n)`（1-based），返回该秩的样本值。

    空样本或 `q ∉ (0, 1]` 显式失败 —— 没有样本就不给数字，不用 0 冒充。
    """
    if not samples:
        raise MetricsError("分位数需要至少一个样本")
    if not 0.0 < quantile <= 1.0:
        raise MetricsError(f"分位必须落在 (0, 1]，收到 {quantile!r}")
    ordered = sorted(float(sample) for sample in samples)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def summarize_latencies(samples: Sequence[float]) -> LatencySummary:
    """P50/P95/P99/max 摘要（同一最近秩口径）；空样本显式失败。"""
    if not samples:
        raise MetricsError("延迟摘要需要至少一个样本")
    return LatencySummary(
        count=len(samples),
        p50=percentile(samples, 0.50),
        p95=percentile(samples, 0.95),
        p99=percentile(samples, 0.99),
        maximum=max(float(sample) for sample in samples),
    )


# --------------------------------------------------------------------------------------
# PostgreSQL：连接/锁/WAL/库大小/运行状态并发
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunState:
    """一个 Run 的真实持久状态（控制面探测取证用）。"""

    status: str
    cancel_requested: bool
    queue_stage: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class PostgresSnapshot:
    """一次真实 PG 采样：实例级计数 + 本库 WAL/大小 + 本租户 Run 状态分布。"""

    observed_at: float
    connections_total: int
    connections_active: int
    locks_granted: int
    locks_waiting: int
    xact_commit: int
    deadlocks: int
    temp_bytes: int
    database_bytes: int
    wal_lsn: str
    runs_by_status: Mapping[str, int]
    waiting_runs: int
    running_runs: int
    event_rows: int = 0
    event_bytes: int = 0
    connections_idle: int = 0
    connections_idle_in_transaction: int = 0
    connections_waiting: int = 0
    #: 当前处于 idle-in-transaction 的真实查询与事务年龄（容量诊断；不参与 SLO 判定）
    idle_in_tx_queries: tuple[tuple[str, float], ...] = ()

    @property
    def open_runs(self) -> int:
        """非终态 Run 数（受理后仍在队列/执行/等待里的持久事实）。"""
        return sum(self.runs_by_status.get(status, 0) for status in OPEN_RUN_STATUSES)

    @property
    def idle_connections_outside_transaction(self) -> int:
        """空闲且不在事务里的连接（`idle` 桶；idle-in-tx 单独计，不混入口径）。"""
        return self.connections_idle

    @property
    def state_buckets_total(self) -> int:
        """互斥状态桶求和：idle + idle-in-tx + active（waiting 是 active 的子集）。"""
        return (
            self.connections_idle
            + self.connections_idle_in_transaction
            + self.connections_active
        )


@dataclass(frozen=True, slots=True)
class PostgresRate:
    """两次真实 PG 采样之间的速率（commits/s、WAL/s、事件行/s）。"""

    elapsed_sec: float
    commits_per_sec: float
    wal_bytes_per_sec: float
    event_rows_per_sec: float
    connections_delta: int


@dataclass(frozen=True, slots=True)
class StatementStat:
    """`pg_stat_statements` 的一条真实聚合（扩展不存在时整组显式返回 None）。"""

    query: str
    calls: int
    total_exec_ms: float
    mean_exec_ms: float


def postgres_rate(
    before: PostgresSnapshot,
    after: PostgresSnapshot,
    *,
    wal_growth_bytes: int,
) -> PostgresRate:
    """用真实采样时间差计算速率；零/负窗口与逆序计数显式失败（不静默回 0）。"""
    elapsed = after.observed_at - before.observed_at
    if elapsed <= 0:
        raise MetricsError(f"PG 速率需要正的时间窗口，收到 {elapsed}")
    if wal_growth_bytes < 0:
        raise MetricsError(f"WAL 增长为负：{wal_growth_bytes}")
    commits_delta = after.xact_commit - before.xact_commit
    events_delta = after.event_rows - before.event_rows
    if commits_delta < 0 or events_delta < 0:
        raise MetricsError("PG 累计计数倒退：采样可能跨了实例重置")
    return PostgresRate(
        elapsed_sec=elapsed,
        commits_per_sec=commits_delta / elapsed,
        wal_bytes_per_sec=wal_growth_bytes / elapsed,
        event_rows_per_sec=events_delta / elapsed,
        connections_delta=after.connections_total - before.connections_total,
    )


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


class _GaugePayload(TypedDict):
    """实例/库级采样载荷（与 `PostgresSnapshot` 的同名字段一一对应）。"""

    connections_total: int
    connections_active: int
    locks_granted: int
    locks_waiting: int
    xact_commit: int
    deadlocks: int
    temp_bytes: int
    database_bytes: int
    wal_lsn: str
    event_bytes: int
    connections_idle: int
    connections_idle_in_transaction: int
    connections_waiting: int


class _RunPayload(TypedDict):
    """本租户 Run 状态载荷（与 `PostgresSnapshot` 的同名字段一一对应）。"""

    runs_by_status: Mapping[str, int]
    waiting_runs: int
    running_runs: int
    event_rows: int


def artifact_bytes(root: Path) -> int:
    """共享卷的真实磁盘占用（逐文件求和；根不存在时显式失败）。"""
    if not root.exists():
        raise MetricsError(f"共享卷根不存在：{root}")
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


class PostgresMetrics:
    """真实 PostgreSQL 指标源：每次采样一条独立只读连接，查询全部参数化。"""

    def __init__(self, database_url: str, *, tenant_id: str, artifacts_root: Path | None = None):
        self._database_url = database_url
        self._tenant_id = tenant_id
        self._artifacts_root = artifacts_root

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def snapshot(self) -> PostgresSnapshot:
        return run_async(self._snapshot)

    def run_state(self, run_id: str) -> RunState | None:
        return run_async(lambda: self._run_state(run_id))

    def wal_growth_bytes(self, before: PostgresSnapshot, after: PostgresSnapshot) -> int:
        """两次采样之间的 WAL 增长字节（`pg_wal_lsn_diff`，服务端计算）。"""
        return run_async(lambda: self._wal_growth(before.wal_lsn, after.wal_lsn))

    def rate(self, before: PostgresSnapshot, after: PostgresSnapshot) -> PostgresRate:
        """两次真实采样之间的 commits/s、WAL/s、事件行/s。"""
        return postgres_rate(
            before,
            after,
            wal_growth_bytes=self.wal_growth_bytes(before, after),
        )

    def pg_stat_statements(self) -> tuple[StatementStat, ...] | None:
        """可选 `pg_stat_statements` 聚合；扩展不存在时显式返回 None（不伪造空列表）。"""
        return run_async(self._pg_stat_statements)

    def artifact_bytes(self) -> int:
        if self._artifacts_root is None:
            raise MetricsError("未配置共享卷根：无法采集磁盘占用")
        return artifact_bytes(self._artifacts_root)

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(_dsn(self._database_url), timeout=10)

    async def _snapshot(self) -> PostgresSnapshot:
        connection = await self._connect()
        try:
            gauges = await self._fetch_gauges(connection)
            runs = await self._fetch_runs(connection)
        finally:
            await connection.close()
        return PostgresSnapshot(observed_at=time.monotonic(), **gauges, **runs)

    async def _fetch_gauges(self, connection: asyncpg.Connection) -> _GaugePayload:
        """实例/库级计数：连接、锁（含等待）、事务/死锁/临时字节、库大小与当前 WAL LSN。"""
        counters = await connection.fetchrow(
            "SELECT xact_commit, deadlocks, temp_bytes FROM pg_stat_database "
            "WHERE datname = current_database()"
        )
        activity = await connection.fetchrow(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE state = 'active') AS active, "
            "count(*) FILTER (WHERE state = 'idle') AS idle, "
            "count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction, "
            "count(*) FILTER (WHERE wait_event_type = 'Lock') AS waiting "
            "FROM pg_stat_activity WHERE datname = current_database()"
        )
        locks = await connection.fetchrow(
            "SELECT count(*) FILTER (WHERE l.granted) AS granted, "
            "count(*) FILTER (WHERE NOT l.granted) AS waiting "
            "FROM pg_locks l JOIN pg_database d ON l.database = d.oid "
            "WHERE d.datname = current_database()"
        )
        size = await connection.fetchval("SELECT pg_database_size(current_database())")
        lsn = await connection.fetchval("SELECT pg_current_wal_lsn()::text")
        event_bytes = await connection.fetchval(
            "SELECT pg_total_relation_size('runtime.canonical_event')"
        )
        idle_in_tx = await connection.fetch(
            "SELECT query, EXTRACT(EPOCH FROM (now() - xact_start)) AS age "
            "FROM pg_stat_activity WHERE datname = current_database() "
            "AND state = 'idle in transaction' AND pid <> pg_backend_pid() "
            "ORDER BY xact_start LIMIT 20"
        )
        if (
            counters is None
            or activity is None
            or locks is None
            or size is None
            or lsn is None
            or event_bytes is None
        ):
            raise MetricsError("PG 采样缺少必需列（连接/锁/大小/LSN/事件表）")
        return {
            "connections_total": int(activity["total"]),
            "connections_active": int(activity["active"]),
            "locks_granted": int(locks["granted"]),
            "locks_waiting": int(locks["waiting"]),
            "xact_commit": int(counters["xact_commit"]),
            "deadlocks": int(counters["deadlocks"]),
            "temp_bytes": int(counters["temp_bytes"]),
            "database_bytes": int(size),
            "wal_lsn": str(lsn),
            "event_bytes": int(event_bytes),
            "connections_idle": int(activity["idle"]),
            "connections_idle_in_transaction": int(activity["idle_in_transaction"]),
            "connections_waiting": int(activity["waiting"]),
            "idle_in_tx_queries": tuple(
                (str(row["query"] or "")[:500], float(row["age"] or 0.0))
                for row in idle_in_tx
            ),
        }

    async def _fetch_runs(self, connection: asyncpg.Connection) -> _RunPayload:
        """本租户 Run 状态分布 + 队列等待数 + 执行中数（真实持久事实，租户谓词显式）。"""
        runs = await connection.fetch(
            "SELECT status::text AS status, count(*) AS total FROM runtime.run_record "
            "WHERE tenant_id = $1 AND is_deleted = false GROUP BY status",
            self._tenant_id,
        )
        waiting = await connection.fetchval(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = $1 "
            "AND is_deleted = false AND queue_stage IS NOT NULL "
            "AND status IN ('CREATED', 'RUNNING')",
            self._tenant_id,
        )
        running = await connection.fetchval(
            "SELECT count(*) FROM runtime.run_record WHERE tenant_id = $1 "
            "AND is_deleted = false AND queue_stage IS NULL AND status = 'RUNNING'",
            self._tenant_id,
        )
        event_rows = await connection.fetchval(
            "SELECT count(*) FROM runtime.canonical_event "
            "WHERE tenant_id = $1 AND is_deleted = false",
            self._tenant_id,
        )
        return {
            "runs_by_status": {str(row["status"]): int(row["total"]) for row in runs},
            "waiting_runs": int(waiting or 0),
            "running_runs": int(running or 0),
            "event_rows": int(event_rows or 0),
        }

    async def _run_state(self, run_id: str) -> RunState | None:
        connection = await self._connect()
        try:
            row = await connection.fetchrow(
                "SELECT status::text AS status, cancel_requested, queue_stage, error_code "
                "FROM runtime.run_record WHERE id = $1::uuid AND tenant_id = $2",
                run_id,
                self._tenant_id,
            )
        finally:
            await connection.close()
        if row is None:
            return None
        return RunState(
            status=str(row["status"]),
            cancel_requested=bool(row["cancel_requested"]),
            queue_stage=None if row["queue_stage"] is None else str(row["queue_stage"]),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
        )

    async def _pg_stat_statements(self) -> tuple[StatementStat, ...] | None:
        connection = await self._connect()
        try:
            available = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')"
            )
            if not available:
                return None
            rows = await connection.fetch(
                "SELECT query, calls, total_exec_time, mean_exec_time "
                "FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20"
            )
        except asyncpg.UndefinedTableError:
            return None
        finally:
            await connection.close()
        return tuple(
            StatementStat(
                query=str(row["query"]),
                calls=int(row["calls"]),
                total_exec_ms=float(row["total_exec_time"]),
                mean_exec_ms=float(row["mean_exec_time"]),
            )
            for row in rows
        )

    async def _wal_growth(self, before_lsn: str, after_lsn: str) -> int:
        connection = await self._connect()
        try:
            growth = await connection.fetchval(
                "SELECT pg_wal_lsn_diff($1::text::pg_lsn, $2::text::pg_lsn)",
                after_lsn,
                before_lsn,
            )
        finally:
            await connection.close()
        if growth is None:
            raise MetricsError("pg_wal_lsn_diff 返回空值")
        return int(growth)
