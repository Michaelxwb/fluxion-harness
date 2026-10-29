"""每 chunk 管线 CPU 微基准（P0-1）。

回答一个问题：模型增量从 SSE 字节到"增量事件入队"这段纯 CPU 路径，每个 chunk 到底花多少微秒？
它**不启栈、不连数据库**：只在同一进程里喂合成 OpenAI SSE 帧，走真实的
`OpenAICompatibleProvider._parse_stream` 与真实的 `_ExecutorEventEmitter`（含 `DeltaWindow`、
有界队列握手），把"chunk 管线"从"每事件 DB 写"里剥出来。

场景：

| 场景 | 覆盖 |
|---|---|
| `parse_only` | httpx 分行 + JSON 解析 + 文本累积 + 回调（空回调） |
| `parse_window` | 上面 + `DeltaWindow.add`（不 flush） |
| `emitter_realistic` | 上面 + 真实窗口/队列/消费者（默认 300ms / 8192B，50ms 一帧） |
| `emitter_flush_each` | 最坏情况：每个 chunk 都到界，每次都要入队并等消费 |

判读：把 `us_per_chunk` 与每事件持久化成本（cProfile 约 0.5–0.6ms CPU / 实测 125ms 墙钟）
对比；再按目标拓扑（1000 用户 × 8 轮 / 600s，2 个 runtime 进程）折算每进程 CPU 占比。

用法：

```bash
uv run python scripts/capacity/chunk_bench.py
uv run python scripts/capacity/chunk_bench.py --chunks 200 --repeats 20 --json-out /tmp/chunk.json
```

口径与已知坑见 `scripts/capacity/PLAYBOOK.md`。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
from muad_agent_core.model.openai_provider import OpenAICompatibleProvider
from muad_agent_runtime.application.executor import ExecutorRequest, _ExecutorEventEmitter
from muad_agent_runtime.application.model_call_trace import ModelCallTrace
from muad_agent_runtime.application.run_events import DeltaWindow
from muad_common import SharedSettings

#: 探针同款增量：20 chunks/s × 10s，每块 12 字节。
DEFAULT_CHUNKS = 200
DEFAULT_CHUNK_INTERVAL_MS = 50
DEFAULT_REPEATS = 15
#: 目标拓扑折算：1000 用户 × 8 轮 / 600s，2 个 runtime 进程（见分析文档 §0.4）。
TARGET_TURNS_PER_SEC = 1000 * 8 / 600
TARGET_RUNTIME_PROCESSES = 2
CHUNK_TEXT = "容量分片{index:03d}-"
MODEL_NAME = "capacity-probe"


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: str
    chunks: int
    repeats: int
    events_per_run: float
    us_per_chunk: float
    ms_per_run: float
    runs_per_sec: float

    def cpu_percent_at(self, chunks_per_sec: float) -> float:
        """给定 chunk/s，折算这段管线占单核的百分比。"""
        return self.us_per_chunk * chunks_per_sec / 10_000.0


@dataclass(frozen=True, slots=True)
class BenchReport:
    chunks: int
    chunk_interval_ms: int
    repeats: int
    rows: tuple[ScenarioResult, ...]
    settings_ms: float | None


def _sse_body(chunks: int) -> bytes:
    """按探针的真实帧形状（`_delta_payload`）拼 SSE 正文。"""
    lines: list[str] = []
    for index in range(chunks):
        payload = {
            "id": "chatcmpl-capacity-probe",
            "object": "chat.completion.chunk",
            "model": MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": CHUNK_TEXT.format(index=index)},
                    "finish_reason": None,
                }
            ],
        }
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    final = {
        "id": "chatcmpl-capacity-probe",
        "object": "chat.completion.chunk",
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": chunks, "total_tokens": 10 + chunks},
    }
    lines.append(f"data: {json.dumps(final, ensure_ascii=False)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": "text/event-stream"},
    )


async def _noop_delta(_text: str) -> None:
    return None


async def _drain(queue: asyncio.Queue[Any]) -> None:
    """队列消费者替身：只做握手，不写数据库——把 CPU 成本与持久化成本分开。"""
    while True:
        await queue.get()
        queue.task_done()


class _FakeClock:
    """注入 `DeltaWindow` 的单调时钟：按帧节拍推进，复现真实窗口的 flush 频率。"""

    def __init__(self, step_sec: float) -> None:
        self._now = 0.0
        self._step = step_sec

    def __call__(self) -> float:
        return self._now

    def advance(self) -> None:
        self._now += self._step


def _window(*, flush_bytes: int = 8192, clock: Callable[[], float] | None = None) -> DeltaWindow:
    """与 `delta_window_from_settings` 同参（生产默认 300ms / 8192B，首块立即 flush）。"""
    return DeltaWindow(
        flush_interval_sec=0.3,
        flush_bytes=flush_bytes,
        clock=clock or time.monotonic,
        flush_first=True,
    )


async def _parse_only(provider: OpenAICompatibleProvider, body: bytes) -> int:
    await provider._parse_stream(_response(body), _noop_delta)
    return 0


async def _parse_window(provider: OpenAICompatibleProvider, body: bytes) -> int:
    window = _window()

    async def callback(text: str) -> None:
        window.add(text)

    await provider._parse_stream(_response(body), callback)
    return 0


async def _parse_emitter(
    provider: OpenAICompatibleProvider,
    body: bytes,
    *,
    flush_each: bool,
    chunk_interval_ms: int,
) -> int:
    """真实 emitter 路径：窗口 → 有界队列 → 内存消费者（含 `queue.join()` 往返）。"""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    clock = _FakeClock(chunk_interval_ms / 1000.0)
    window = _window(flush_bytes=1 if flush_each else 8192, clock=clock)
    emitter = _ExecutorEventEmitter(
        queue=queue,
        window=window,
        request=cast(ExecutorRequest, SimpleNamespace(run_context=None)),
        model_trace=ModelCallTrace(audit_enabled=False),
    )
    emitter._model_trace.begin()
    consumer = asyncio.create_task(_drain(queue))
    emitted = 0
    push = emitter.push

    async def counting_push(event: Any) -> None:
        nonlocal emitted
        emitted += 1
        await push(event)

    emitter.push = counting_push  # type: ignore[method-assign]

    async def callback(text: str) -> None:
        clock.advance()
        await emitter.on_delta(text)

    try:
        await provider._parse_stream(_response(body), callback)
        await emitter.flush_deltas()
    finally:
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
    return emitted


async def _measure(
    name: str,
    run: Callable[[], Awaitable[int]],
    *,
    chunks: int,
    repeats: int,
) -> ScenarioResult:
    events: list[int] = []
    started = time.perf_counter()
    for _ in range(repeats):
        events.append(await run())
    elapsed = time.perf_counter() - started
    per_run = elapsed / repeats
    return ScenarioResult(
        scenario=name,
        chunks=chunks,
        repeats=repeats,
        events_per_run=statistics.fmean(events) if events else 0.0,
        us_per_chunk=per_run / chunks * 1_000_000,
        ms_per_run=per_run * 1000.0,
        runs_per_sec=1.0 / per_run if per_run else 0.0,
    )


async def run_bench(
    *,
    chunks: int = DEFAULT_CHUNKS,
    repeats: int = DEFAULT_REPEATS,
    chunk_interval_ms: int = DEFAULT_CHUNK_INTERVAL_MS,
    measure_settings: bool = True,
) -> BenchReport:
    if chunks < 1:
        raise ValueError("chunks must be positive")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if chunk_interval_ms < 0:
        raise ValueError("chunk interval must not be negative")

    body = _sse_body(chunks)
    client = httpx.AsyncClient()
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:1",
        model=MODEL_NAME,
        api_key=None,
        client=client,
    )
    try:
        rows = [
            await _measure(
                "parse_only",
                lambda: _parse_only(provider, body),
                chunks=chunks,
                repeats=repeats,
            ),
            await _measure(
                "parse_window",
                lambda: _parse_window(provider, body),
                chunks=chunks,
                repeats=repeats,
            ),
            await _measure(
                "emitter_realistic",
                lambda: _parse_emitter(
                    provider,
                    body,
                    flush_each=False,
                    chunk_interval_ms=chunk_interval_ms,
                ),
                chunks=chunks,
                repeats=repeats,
            ),
            await _measure(
                "emitter_flush_each",
                lambda: _parse_emitter(
                    provider,
                    body,
                    flush_each=True,
                    chunk_interval_ms=chunk_interval_ms,
                ),
                chunks=chunks,
                repeats=repeats,
            ),
        ]
    finally:
        await client.aclose()

    settings_ms: float | None = None
    if measure_settings:
        started = time.perf_counter()
        for _ in range(3):
            SharedSettings()
        settings_ms = (time.perf_counter() - started) / 3 * 1000.0

    return BenchReport(
        chunks=chunks,
        chunk_interval_ms=chunk_interval_ms,
        repeats=repeats,
        rows=tuple(rows),
        settings_ms=settings_ms,
    )


def _format(report: BenchReport) -> str:
    chunks_per_sec = TARGET_TURNS_PER_SEC * report.chunks
    per_process = chunks_per_sec / TARGET_RUNTIME_PROCESSES
    lines = [
        f"chunks={report.chunks} interval={report.chunk_interval_ms}ms repeats={report.repeats}",
        f"target: {TARGET_TURNS_PER_SEC:.2f} turns/s total, "
        f"{chunks_per_sec:.0f} chunks/s, {per_process:.0f} chunks/s per runtime process",
        "",
        f"{'scenario':<22}{'us/chunk':>10}{'ms/run':>10}{'runs/s':>10}{'events/run':>12}{'CPU%*':>9}",
    ]
    for row in report.rows:
        lines.append(
            f"{row.scenario:<22}{row.us_per_chunk:>10.2f}{row.ms_per_run:>10.2f}"
            f"{row.runs_per_sec:>10.1f}{row.events_per_run:>12.1f}"
            f"{row.cpu_percent_at(per_process):>9.1f}"
        )
    lines.append("")
    lines.append("* CPU% = 该场景在目标拓扑下每进程的折算占用（单核百分比，不含 DB/序列化/网络）")
    if report.settings_ms is not None:
        lines.append(f"SharedSettings() = {report.settings_ms:.2f} ms/次")
    best = report.rows[0]
    if best.us_per_chunk > 0:
        lines.append(
            f"纯解析单核上限 ≈ {1_000_000 / best.us_per_chunk:.0f} chunks/s；"
            f"整条 chunk 管线（含队列）上限 ≈ "
            f"{1_000_000 / report.rows[-1].us_per_chunk:.0f} chunks/s"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每 chunk 管线 CPU 微基准（不启栈、不连 DB）")
    parser.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS, help="每轮增量块数（默认 200）")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="重复轮数（默认 15）")
    parser.add_argument(
        "--chunk-interval-ms",
        type=int,
        default=DEFAULT_CHUNK_INTERVAL_MS,
        help="窗口时钟推进节拍，对应探针 20 chunks/s（默认 50）",
    )
    parser.add_argument("--no-settings", action="store_true", help="跳过 SharedSettings() 计时")
    parser.add_argument("--json-out", type=Path, help="把结果写成 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        run_bench(
            chunks=args.chunks,
            repeats=args.repeats,
            chunk_interval_ms=args.chunk_interval_ms,
            measure_settings=not args.no_settings,
        )
    )
    print(_format(report))
    if args.json_out is not None:
        payload = {
            "chunks": report.chunks,
            "chunk_interval_ms": report.chunk_interval_ms,
            "repeats": report.repeats,
            "settings_ms": report.settings_ms,
            "target": {
                "turns_per_sec": TARGET_TURNS_PER_SEC,
                "runtime_processes": TARGET_RUNTIME_PROCESSES,
                "chunks_per_sec": TARGET_TURNS_PER_SEC * report.chunks,
            },
            "rows": [asdict(row) for row in report.rows],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
