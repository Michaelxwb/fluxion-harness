"""[B-212] Worker 指标目录（scheduled-task-v2 口径）：真实 `/metrics` + 真实调度路径打点 + label 卫生。

迁移自 v1：`ScheduleService` / `create_schedule_payload` / `intent_key` / delivery 列已随
TASK-002 退役；本文件改用 v2 的 Schedule + Revision + Fire + Execution 事实。

不得 Mock 的真实边界：
- 真实 uvicorn 单进程（127.0.0.1 真实 socket，非 ASGI 内存传输）+ 真实 Worker 路由栈；
- 真实 PostgreSQL（v2 调度表、Fire 唯一键、Execution 冻结）；
- 真实 SchedulerLoop / 指标注册表 → `GET /metrics`。
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import uvicorn
from fastapi import FastAPI
from muad_agent_worker.main import app as worker_app
from muad_agent_worker.metrics import CATALOG
from muad_agent_worker.scheduler.client import ScheduleMaterialsError

from agent_worker.conftest import TenantContext
from agent_worker.test_scheduled_v2_fire import (
    SLOT,
    MaterialsClient,
    loop_for,
    materials_for,
    seed,
)

METRICS_PATH = "/metrics"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
LISTEN_TIMEOUT_SEC = 15.0

#: 本任务落地的 v2 目录（名称与 label 必须与 design §3.5.12 一致）。
WORKER_METRIC_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("scheduled_fire_total", "counter", ("status",)),
    ("schedule_fire_created_total", "counter", ()),
    ("schedule_fire_duplicate_total", "counter", ()),
    ("schedule_fire_skipped_total", "counter", ("reason",)),
    ("schedule_admission_failed_total", "counter", ("reason",)),
    ("schedule_timezone_resolution_error_total", "counter", ()),
    ("schedule_active_count", "gauge", ()),
    ("schedule_needs_attention_count", "gauge", ()),
    ("schedule_due_lag_seconds", "gauge", ()),
    ("task_waiting_human_count", "gauge", ()),
    ("task_waiting_external_count", "gauge", ()),
    ("task_human_timeout_total", "counter", ()),
    ("task_snapshot_bytes", "gauge", ()),
    ("task_execution_duration_seconds", "gauge", ("type",)),
    ("task_async_operation_total", "counter", ("status",)),
    ("task_query_only_total", "counter", ()),
    ("worker_loop_tick_total", "counter", ("loop", "status")),
    ("worker_loop_stale_total", "counter", ("loop",)),
    ("worker_loop_restart_total", "counter", ("loop",)),
    ("context_compaction_total", "counter", ("layer", "status")),
    ("delivery_total", "counter", ("status",)),
)

SENSITIVE_LABEL_MARKERS = (
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
)
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
CREDENTIAL_PATTERN = re.compile(
    r"(bearer\s|basic\s|sk-[A-Za-z0-9]{8,}|[A-Za-z0-9+/]{40,}={0,2}$)", re.IGNORECASE
)
SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)
BODY_CANARY = "b212-canary-消息正文-不得进指标"


def _parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    """解析 Prometheus 文本样本（label 取值不含逗号，够本用例断言用）。"""
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None:
            continue
        labels: dict[str, str] = {}
        raw_labels = matched.group("labels")
        if raw_labels:
            for pair in raw_labels[1:-1].split(","):
                name, _, value = pair.partition("=")
                labels[name.strip()] = value.strip().strip('"')
        samples.append((matched.group("name"), labels, float(matched.group("value"))))
    return samples


def _sample_value(
    samples: list[tuple[str, dict[str, str], float]], name: str, labels: dict[str, str]
) -> float | None:
    for sample_name, sample_labels, value in samples:
        if sample_name == name and all(sample_labels.get(key) == item for key, item in labels.items()):
            return value
    return None


def _assert_catalog_declared(text: str) -> None:
    """目录在无流量时也必须可抓取（`# TYPE` 行由服务安装时声明）。"""
    missing = [
        name
        for name, kind, _labels in WORKER_METRIC_CATALOG
        if f"# TYPE {name} {kind}" not in text
    ]
    assert not missing, f"Worker 指标目录缺失：{missing}\n{text}"


def _assert_label_hygiene(samples: list[tuple[str, dict[str, str], float]], text: str) -> None:
    """label 名不得命中敏感标记；label 值不得落入资源 ID（UUID）或凭据形态。"""
    for name, labels, _value in samples:
        for key, value in labels.items():
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in key.lower(), f"指标 label 名命中敏感标记 {marker}：{name}{labels}"
            assert not UUID_PATTERN.search(value), f"指标 label 值落入资源 ID：{name}{labels}"
            assert not CREDENTIAL_PATTERN.search(value), f"指标 label 值呈凭据形态：{name}{labels}"


@asynccontextmanager
async def _serve_http(target: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口（真实 socket）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, f"{target.title} 未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


async def _scrape(http: httpx.AsyncClient) -> tuple[list[tuple[str, dict[str, str], float]], str]:
    response = await http.get(METRICS_PATH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    return _parse_samples(response.text), response.text


async def test_b212_metrics_endpoint_exposes_v2_worker_catalog(tenant: TenantContext) -> None:
    """B-212：真实 `/metrics` 暴露 v2 目录，且样本 label 无敏感形状。"""
    async with _serve_http(worker_app) as http:
        samples, text = await _scrape(http)

    _assert_catalog_declared(text)
    _assert_label_hygiene(samples, text)
    assert tenant.tenant_id not in text, "指标不得落入租户标识"


async def test_b212_scheduler_fire_and_snapshot_metrics_come_from_real_path(
    tenant: TenantContext,
) -> None:
    """B-212：真实 Schedule→Fire→Execution 路径产出 created/due-lag/snapshot 样本。"""
    seeded = await seed(tenant, next_fire_at=SLOT)
    async with _serve_http(worker_app) as http:
        before, _text = await _scrape(http)
        created_before = _sample_value(before, "schedule_fire_created_total", {}) or 0

        loop = loop_for(tenant, MaterialsClient(materials_for()))
        assert await loop.run_once(now=SLOT + timedelta(seconds=5)) is not None

        samples, text = await _scrape(http)

    assert (_sample_value(samples, "schedule_fire_created_total", {}) or 0) >= created_before + 1, text
    assert (_sample_value(samples, "scheduled_fire_total", {"status": "EXECUTION_CREATED"}) or 0) >= 1
    assert _sample_value(samples, "schedule_due_lag_seconds", {}) is not None, text
    assert (_sample_value(samples, "task_snapshot_bytes", {}) or 0) > 0, text

    _assert_label_hygiene(samples, text)
    assert str(seeded.schedule_id) not in text, "指标落入具体 Schedule ID"
    assert str(seeded.owner_user_id) not in text, "指标落入用户标识"


async def test_b212_misfire_and_admission_failures_are_labelled_by_closed_reasons(
    tenant: TenantContext,
) -> None:
    """B-212：MISFIRE 与准入失败分别计数；label 只取 `SkipReason` / 目录错误码。"""
    await seed(tenant, next_fire_at=SLOT - timedelta(hours=2))
    failure = ScheduleMaterialsError(
        "denied", code="SCHEDULE_DEPENDENCY_MISSING", data={"admission_code": "SKILL_DISABLED"}
    )
    await seed(tenant, next_fire_at=SLOT)
    async with _serve_http(worker_app) as http:
        before, _text = await _scrape(http)
        skipped_before = _sample_value(
            before, "schedule_fire_skipped_total", {"reason": "MISFIRE"}
        ) or 0
        admission_before = _sample_value(
            before, "schedule_admission_failed_total", {"reason": "SKILL_DISABLED"}
        ) or 0

        await loop_for(tenant, MaterialsClient()).run_once(now=SLOT + timedelta(seconds=1))
        await loop_for(tenant, MaterialsClient(failure=failure)).run_once(
            now=SLOT + timedelta(seconds=1)
        )

        samples, text = await _scrape(http)

    assert (
        _sample_value(samples, "schedule_fire_skipped_total", {"reason": "MISFIRE"}) or 0
    ) >= skipped_before + 1, text
    assert (
        _sample_value(samples, "schedule_admission_failed_total", {"reason": "SKILL_DISABLED"})
        or 0
    ) >= admission_before + 1, text
    assert BODY_CANARY not in text, "指标落入消息正文"
    _assert_label_hygiene(samples, text)


def test_b212_declared_catalog_matches_design_and_label_names_are_hygienic() -> None:
    """B-212：服务声明的目录与设计 §3.5.12 逐项一致，且 label 名不含敏感标记。"""
    declared = {name: (kind, labels) for name, kind, labels, _help in CATALOG}
    for name, kind, labels in WORKER_METRIC_CATALOG:
        assert declared.get(name) == (kind, labels), f"{name} 的声明与设计不一致：{declared.get(name)}"
        for label in labels:
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in label.lower(), f"{name} 的 label 名命中敏感标记：{label}"
