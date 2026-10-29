#!/usr/bin/env python3
"""容量诊断小工具：读分位数、采进程 CPU、采 idle-in-tx、切换 synchronous_commit。

口径与判读顺序见 `scripts/capacity/PLAYBOOK.md`。
只依赖标准库（`pgsample` / `sync-commit` 额外用 asyncpg）。

    uv run python scripts/capacity/diag.py metrics --url http://127.0.0.1:8001/metrics
    uv run python scripts/capacity/diag.py cpu --out /tmp/p0-cpu.log
    uv run python scripts/capacity/diag.py pgsample --interval 0.5
    uv run python scripts/capacity/diag.py sync-commit off
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

#: 采样行：`name{labels} value`（与 Prometheus 文本同形；注释/空行由调用方跳过）。
_SAMPLE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[0-9eE+\-.]+)$"
)
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
#: uvicorn 进程的模块名 → 单元短名（`ps` 行里按命令文本匹配）。
_UNITS: tuple[tuple[str, str], ...] = (
    ("muad_agent_runtime", "runtime"),
    ("muad_agent_worker", "worker"),
    ("muad_im_gateway", "gateway"),
    ("muad_im_connector", "connector"),
    ("muad_console_platform", "console"),
)


class DiagError(RuntimeError):
    """诊断工具的显式失败（坏文本、缺参数、权限不允许）：绝不静默回 0。"""


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float


@dataclass(frozen=True, slots=True)
class HistogramSummary:
    """累计桶复算出的时长摘要（秒）；分位数取桶上界，即桶粒度近似。"""

    count: int
    mean: float
    p50: float
    p95: float
    p99: float
    top: float


def parse_metrics(text: str, source: str = "/metrics") -> tuple[Sample, ...]:
    """严格解析 Prometheus 文本：坏行/重复序列/非有限值显式失败（没有样本也失败）。"""
    samples: list[Sample] = []
    seen: set[tuple[str, str]] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if match is None:
            raise DiagError(f"{source}:{number} 不是合法样本行")
        try:
            value = float(match.group("value"))
        except ValueError as exc:  # pragma: no cover - 正则已限形
            raise DiagError(f"{source}:{number} 的样本值不是数字") from exc
        labels = {
            name: value.replace('\\"', '"').replace("\\\\", "\\")
            for name, value in _LABEL.findall(match.group("labels") or "")
        }
        key = (match.group("name"), json.dumps(labels, sort_keys=True))
        if key in seen:
            raise DiagError(f"{source}:{number} 的样本重复出现")
        seen.add(key)
        samples.append(Sample(match.group("name"), labels, value))
    if not samples:
        raise DiagError(f"{source} 为空：没有可用样本")
    return tuple(samples)


def _bound_seconds(suffix: str) -> float:
    for unit, scale in (("us", 1e-6), ("ms", 1e-3), ("s", 1.0)):
        if suffix.endswith(unit):
            try:
                return float(suffix[: -len(unit)]) * scale
            except ValueError as exc:
                raise DiagError(f"桶上界无法解析：{suffix!r}") from exc
    raise DiagError(f"桶上界缺少单位（us/ms/s）：{suffix!r}")


def _dimension(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))


def histogram_summary(
    samples: tuple[Sample, ...], family: str, *, labels: dict[str, str] | None = None
) -> HistogramSummary | None:
    """从 `<family>_le_<上界>`/`_sum`/`_count` 复算分位数；窗口内无样本返回 None。

    `labels` 是维度过滤（如 `{"stage": "persist"}`）。同一 family 下有多组维度而调用方
    没有过滤时显式失败——跨维度相加会把不同口径的桶混在一起。
    """
    wanted = dict(labels or {})
    prefix = family + "_le_"
    buckets: dict[tuple[tuple[str, str], ...], dict[float, float]] = {}
    counts: dict[tuple[tuple[str, str], ...], float] = {}
    sums: dict[tuple[tuple[str, str], ...], float] = {}
    for sample in samples:
        if any(sample.labels.get(name) != value for name, value in wanted.items()):
            continue
        key = _dimension(sample.labels)
        if sample.name == family + "_count":
            counts[key] = counts.get(key, 0.0) + sample.value
        elif sample.name == family + "_sum":
            sums[key] = sums.get(key, 0.0) + sample.value
        elif sample.name.startswith(prefix):
            bound = _bound_seconds(sample.name[len(prefix):])
            buckets.setdefault(key, {})
            buckets[key][bound] = buckets[key].get(bound, 0.0) + sample.value
    dimensions = set(counts) | set(sums) | set(buckets)
    if not dimensions:
        return None
    if len(dimensions) > 1:
        raise DiagError(
            f"{family} 命中 {len(dimensions)} 组维度：请用 labels 过滤（如 --stage persist）"
        )
    key = next(iter(dimensions))
    count = counts.get(key, 0.0)
    if count <= 0:
        return None
    bounds = sorted(buckets.get(key, {}).items())
    if not bounds:
        return None

    def quantile(q: float) -> float:
        target = q * count
        return next(
            (bound for bound, cumulative in bounds if cumulative >= target - 1e-9), bounds[-1][0]
        )

    return HistogramSummary(
        count=int(round(count)),
        mean=sums.get(key, 0.0) / count,
        p50=quantile(0.50),
        p95=quantile(0.95),
        p99=quantile(0.99),
        top=bounds[-1][0],
    )


def _format(summary: HistogramSummary | None) -> str:
    if summary is None:
        return "no samples"
    return (
        f"n={summary.count} mean={summary.mean * 1000:.1f}ms "
        f"p50<={summary.p50 * 1000:.1f}ms p95<={summary.p95 * 1000:.1f}ms "
        f"p99<={summary.p99 * 1000:.1f}ms max<={summary.top * 1000:.1f}ms"
    )


def fetch_metrics(url: str, timeout: float = 10.0) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except OSError as exc:
        raise DiagError(f"抓取 {url} 失败：{type(exc).__name__}") from exc
    return bytes(payload).decode("utf-8")


def cmd_metrics(args: argparse.Namespace) -> int:
    text = fetch_metrics(args.url)
    samples = parse_metrics(text, source=args.url)
    dimensions = [{"stage": stage} for stage in args.stage] if args.stage else [None]
    for family in args.family:
        for labels in dimensions:
            try:
                summary = histogram_summary(samples, family, labels=labels)
            except DiagError as exc:
                print(f"[{family}{labels or ''}] {exc}")
                continue
            suffix = f" stage={labels['stage']}" if labels else ""
            print(f"[{family}{suffix}] {_format(summary)}")
    return 0


def parse_ps_line(line: str) -> tuple[int, float, str] | None:
    """`ps -eo pid,pcpu,rss,etime,command` 的一行 → (pid, cpu%, 单元短名)；非本栈进程返回 None。"""
    parts = line.split(None, 4)
    if len(parts) < 5:
        return None
    pid_text, cpu_text, _rss, _etime, command = parts
    unit = next((short for marker, short in _UNITS if marker in command), None)
    if unit is None or "uvicorn" not in command:
        return None
    try:
        return int(pid_text), float(cpu_text), unit
    except ValueError:
        return None


def _ps_lines() -> list[str]:
    output = subprocess.run(
        ["ps", "-eo", "pid,pcpu,rss,etime,command"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return output.splitlines()[1:]


def _percentile(values: list[float], q: float) -> float:
    """最近秩分位数（与 `tests/capacity` 同口径）：rank = ceil(q × n)。"""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def cmd_cpu(args: argparse.Namespace) -> int:
    """按 1s（可配）采样本栈进程 CPU；Ctrl-C 结束并打印负载窗口统计。"""
    collected: dict[tuple[int, str], list[float]] = {}
    out = Path(args.out) if args.out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration if args.duration > 0 else None
    print("采样中（Ctrl-C 结束）…", file=sys.stderr)
    try:
        while deadline is None or time.monotonic() < deadline:
            stamp = f"{time.time():.3f}"
            rows = [(stamp, line) for line in _ps_lines()]
            if out is not None:
                with out.open("a", encoding="utf-8") as handle:
                    for _stamp, line in rows:
                        handle.write(line + "\n")
                    handle.write("---\n")
            for _stamp, line in rows:
                parsed = parse_ps_line(line)
                if parsed is None:
                    continue
                pid, cpu, unit = parsed
                collected.setdefault((pid, unit), []).append(cpu)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    if not collected:
        print("没有采到 uvicorn 进程（检查服务是否在跑）", file=sys.stderr)
        return 1
    print("pid     unit      max     p90     mean   samples")
    for (pid, unit), values in sorted(collected.items(), key=lambda item: -max(item[1])):
        print(
            f"{pid:<7} {unit:<9} {max(values):6.1f}% {_percentile(values, 0.9):6.1f}% "
            f"{sum(values) / len(values):6.1f}% {len(values):7d}"
        )
    return 0


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _database_url(args: argparse.Namespace) -> str:
    url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        # 本地开发通常只在 .env 里配置；工具直接复用仓库的 typed 设置，避免再抄一份默认值。
        from muad_common import SharedSettings

        url = SharedSettings().database_url or ""
    if not url:
        raise DiagError("缺少数据库地址：给 --database-url、DATABASE_URL 或配置 .env")
    return _dsn(url)


async def _pgsample(dsn: str, interval: float, duration: float) -> None:
    import asyncpg  # type: ignore[import-untyped]

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        async def commits() -> int:
            return int(
                await connection.fetchval(
                    "SELECT xact_commit FROM pg_stat_database WHERE datname = current_database()"
                )
            )

        previous, previous_at = await commits(), time.monotonic()
        deadline = previous_at + duration if duration > 0 else None
        print("commits/s idle_in_tx  top idle-in-tx (age, query)")
        try:
            while deadline is None or time.monotonic() < deadline:
                await asyncio.sleep(interval)
                now = time.monotonic()
                current = await commits()
                rows = await connection.fetch(
                    "SELECT query, EXTRACT(EPOCH FROM (now() - xact_start)) AS age "
                    "FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state = 'idle in transaction' AND pid <> pg_backend_pid() "
                    "ORDER BY xact_start DESC LIMIT 3"
                )
                rate = (current - previous) / max(now - previous_at, 1e-6)
                previous, previous_at = current, now
                top = " | ".join(
                    f"{float(row['age'] or 0):.2f}s {(row['query'] or '').strip()[:80]}" for row in rows
                )
                print(f"{rate:9.1f} {len(rows):10d}  {top}")
        except KeyboardInterrupt:
            return
    finally:
        await connection.close()


def cmd_pgsample(args: argparse.Namespace) -> int:
    asyncio.run(_pgsample(_database_url(args), args.interval, args.duration))
    return 0


async def _sync_commit(dsn: str, action: str, role: str | None) -> None:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        target = role or str(await connection.fetchval("SELECT current_user"))
        quoted = '"' + target.replace('"', '""') + '"'
        if action == "reset":
            await connection.execute(f"ALTER ROLE {quoted} RESET synchronous_commit")
        else:
            await connection.execute(
                f"ALTER ROLE {quoted} SET synchronous_commit = {'on' if action == 'on' else 'off'}"
            )
    finally:
        await connection.close()
    fresh = await asyncpg.connect(dsn, timeout=10)
    try:
        current = await fresh.fetchval("SHOW synchronous_commit")
    finally:
        await fresh.close()
    print(f"{target}: 新会话 synchronous_commit={current}（已运行的连接不受影响）")


def cmd_sync_commit(args: argparse.Namespace) -> int:
    asyncio.run(_sync_commit(_database_url(args), args.action, args.role))
    return 0


DEFAULT_FAMILIES = (
    "db_pool_checkout_seconds",
    "db_connection_hold_seconds",
    "run_persist_event_seconds",
    "run_checkpoint_seconds",
    "run_finalize_seconds",
    "run_consume_seconds",
    "run_admission_stage_seconds",
    "model_call_seconds",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diag.py", description="容量诊断小工具")
    sub = parser.add_subparsers(dest="command", required=True)

    metrics = sub.add_parser("metrics", help="从累计桶读分位数")
    metrics.add_argument("--url", default="http://127.0.0.1:8001/metrics")
    metrics.add_argument("--family", action="append", default=None)
    metrics.add_argument("--stage", action="append", default=None)
    metrics.set_defaults(func=cmd_metrics)

    cpu = sub.add_parser("cpu", help="采样本栈进程 CPU")
    cpu.add_argument("--interval", type=float, default=1.0)
    cpu.add_argument("--duration", type=float, default=0.0, help="0 表示直到 Ctrl-C")
    cpu.add_argument("--out", default=None, help="原始 ps 行写入文件（JSONL-ish）")
    cpu.set_defaults(func=cmd_cpu)

    pg = sub.add_parser("pgsample", help="采 commits/s 与 idle-in-tx")
    pg.add_argument("--database-url", default=None)
    pg.add_argument("--interval", type=float, default=0.5)
    pg.add_argument("--duration", type=float, default=0.0)
    pg.set_defaults(func=cmd_pgsample)

    sync = sub.add_parser("sync-commit", help="切换角色级 synchronous_commit")
    sync.add_argument("action", choices=("on", "off", "reset"))
    sync.add_argument("--database-url", default=None)
    sync.add_argument("--role", default=None, help="默认当前连接用户")
    sync.set_defaults(func=cmd_sync_commit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "family", None) is None and args.command == "metrics":
        args.family = list(DEFAULT_FAMILIES)
    try:
        return int(args.func(args))
    except DiagError as exc:
        print(f"diag: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
