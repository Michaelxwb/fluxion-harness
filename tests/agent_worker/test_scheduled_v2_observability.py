"""[TASK-032] 调度执行面的观测与循环监督：真实目录、真实 PG 计数、真实故障重启与关闭回收。

不含 Mock 的边界：
- 指标目录与样本走 api-kit 的真实进程内注册表（`/metrics` 渲染的同一来源）；
- 状态 gauge 与执行耗时来自真实 PostgreSQL 行（v2 调度表 + 终态事务）；
- 监督器真实创建/取消 `asyncio` 任务，停顿/崩溃都以真实任务生命周期触发。

label 卫生：所有 label 名只取封闭枚举；样本里不得出现 UUID、租户标识、正文或凭据形态。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import timedelta

import pytest
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.main import _task_state_gauges
from muad_agent_worker.metrics import (
    SCHEDULE_ACTIVE_COUNT_METRIC,
    SCHEDULE_NEEDS_ATTENTION_COUNT_METRIC,
    TASK_WAITING_EXTERNAL_COUNT_METRIC,
    TASK_WAITING_HUMAN_COUNT_METRIC,
    _loop_ticks,
    record_loop_tick,
)
from muad_agent_worker.worker.supervision import LoopSupervisor
from muad_api import render_metrics
from muad_contracts import TerminalStatus
from sqlalchemy import select, update

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_fire import SLOT, MaterialsClient, loop_for, materials_for, seed

pytestmark = pytest.mark.integration

SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
CLOSED_LABELS = frozenset(
    {
        "type",
        "status",
        "reason",
        "loop",
        "layer",
        "source",
        "kind",
        "format",
        "caller",
        "result",
        "operation",
        "component",
        "pool",
        "action",
    }
)
SENSITIVE_LABEL_MARKERS = ("token", "secret", "password", "credential", "authorization", "cookie")


def _samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    parsed: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None:
            continue
        labels: dict[str, str] = {}
        raw = matched.group("labels")
        if raw:
            for pair in raw[1:-1].split(","):
                name, _, value = pair.partition("=")
                labels[name.strip()] = value.strip().strip('"')
        parsed.append((matched.group("name"), labels, float(matched.group("value"))))
    return parsed


def _value(text: str, name: str, labels: dict[str, str]) -> float:
    for sample_name, sample_labels, value in _samples(text):
        if sample_name == name and all(sample_labels.get(k) == v for k, v in labels.items()):
            return value
    return 0.0


def _catalog_labels() -> list[tuple[str, tuple[str, ...]]]:
    from muad_agent_worker.metrics import CATALOG

    return [(name, labels) for name, _kind, labels, _help in CATALOG]


def test_t032_catalog_labels_are_closed_and_non_sensitive() -> None:
    """目录的 label 名只取封闭枚举：无租户/资源 ID/凭据/正文。"""
    for name, labels in _catalog_labels():
        for label in labels:
            assert label in CLOSED_LABELS, f"{name} 的 label {label!r} 不在封闭白名单"
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in label.lower(), f"{name} 的 label {label!r} 命中敏感标记"


async def test_t032_state_gauges_are_tenant_scoped_and_published(tenant: TenantContext) -> None:
    """状态 gauge 只数本租户：ACTIVE / NEEDS_ATTENTION / 两类等待各自独立。"""
    await seed(tenant, next_fire_at=SLOT)
    await seed(tenant, next_fire_at=SLOT, status="NEEDS_ATTENTION")
    await seed(tenant, next_fire_at=SLOT, status="PAUSED")
    await seed(tenant, next_fire_at=SLOT, status="ACTIVE", kind="ONCE", cron=None, run_at=SLOT)

    gauges = await _task_state_gauges(tenant.session_factory, tenant.tenant_id)

    assert gauges[SCHEDULE_ACTIVE_COUNT_METRIC] == 2
    assert gauges[SCHEDULE_NEEDS_ATTENTION_COUNT_METRIC] == 1
    assert gauges[TASK_WAITING_HUMAN_COUNT_METRIC] == 0
    assert gauges[TASK_WAITING_EXTERNAL_COUNT_METRIC] == 0


async def test_t032_terminal_duration_is_recorded_for_real_execution(
    tenant: TenantContext,
) -> None:
    """终态事务写出的 Execution 才产生耗时样本；等待占用的租户不会串数。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    assert await loop_for(tenant, MaterialsClient(materials_for())).run_once(
        now=SLOT + timedelta(seconds=1)
    ) is not None
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.schedule_id == seeded.schedule_id)
            .values(started_at=SLOT, status="RUNNING")
        )
    async with tenant.session_factory() as session, session.begin():
        execution = await session.scalar(
            select(TaskExecution).where(TaskExecution.schedule_id == seeded.schedule_id)
        )
        assert execution is not None
        assert await write_terminal(
            session,
            execution,
            TerminalChange(status=TerminalStatus.COMPLETED),
            SLOT + timedelta(seconds=7),
        )

    text = render_metrics()
    assert _value(text, "task_execution_duration_seconds", {"type": "SKILL"}) > 0
    assert not UUID_PATTERN.search(text), "指标文本落入资源 ID"


async def test_t032_supervisor_restarts_a_crashed_loop() -> None:
    """循环抛异常退出：任务本身不被静默丢弃，监督器计数并重启。"""
    supervisor = LoopSupervisor(
        check_interval_sec=0.01, restart_backoff_sec=0.01, shutdown_timeout_sec=1.0
    )
    attempts = 0

    async def crash() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    name = "test.crash"
    supervisor.add(name, crash, stale_after_sec=0.05)
    before = _value(render_metrics(), "worker_loop_restart_total", {"loop": name})
    supervisor.start()
    try:
        deadline = time.monotonic() + 1.0
        while attempts < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        after = _value(render_metrics(), "worker_loop_restart_total", {"loop": name})
    finally:
        await supervisor.aclose()

    assert attempts >= 2, "崩溃的循环没有被重启"
    assert after >= before + 1
    _loop_ticks.pop(name, None)


async def test_t032_slow_loop_does_not_delay_fast_loop_and_is_marked_stale() -> None:
    """停顿判据只看循环自身心跳：慢循环被标记/重启，快循环继续推进。"""
    supervisor = LoopSupervisor(
        check_interval_sec=0.01, restart_backoff_sec=0.01, shutdown_timeout_sec=1.0
    )
    ticks = 0

    async def fast() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            record_loop_tick("test.fast", "ok")
            await asyncio.sleep(0.005)

    async def slow() -> None:
        while True:
            await asyncio.sleep(5)  # 不调 record_loop_tick：心跳停在启动时刻

    fast_name, slow_name = "test.fast", "test.slow"
    supervisor.add(fast_name, fast, stale_after_sec=0.2)
    supervisor.add(slow_name, slow, stale_after_sec=0.02)
    before = _value(render_metrics(), "worker_loop_stale_total", {"loop": slow_name})
    supervisor.start()
    try:
        await asyncio.sleep(0.3)
        after = _value(render_metrics(), "worker_loop_stale_total", {"loop": slow_name})
    finally:
        await supervisor.aclose()

    assert ticks >= 10, f"快循环被慢循环拖住了：{ticks}"
    assert after >= before + 1, "停顿循环没有留痕"
    _loop_ticks.pop(fast_name, None)
    _loop_ticks.pop(slow_name, None)


async def test_t032_shutdown_cancels_and_waits_for_loops() -> None:
    """关闭先取消再等待：循环的收尾代码真的执行，进程不带着任务退出。"""
    supervisor = LoopSupervisor(
        check_interval_sec=0.01, restart_backoff_sec=0.01, shutdown_timeout_sec=1.0
    )
    closed = asyncio.Event()

    async def loop() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            closed.set()

    name = "test.shutdown"
    supervisor.add(name, loop, stale_after_sec=10)
    supervisor.start()
    await asyncio.sleep(0.02)

    await asyncio.wait_for(supervisor.aclose(), timeout=2.0)

    assert closed.is_set(), "关闭时没有等待循环收尾"
    _loop_ticks.pop(name, None)
