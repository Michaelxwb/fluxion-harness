"""[B-212] Worker 指标目录：真实 `/metrics` HTTP 端点 + 真实循环调用点递增 + label 卫生。

design 11-audit-observability §3.5 / docs/09 §6.3。
不得 Mock 的真实边界：
- 真实 uvicorn 单进程（127.0.0.1 真实 socket，非 ASGI 内存传输）+ 真实 Worker 路由栈；
- 真实 PostgreSQL（task schema 落库、lease 回收、Schedule 触发与投递状态 CAS）；
- 真实 WorkerLoop / SchedulerLoop / DeliveryLoop + api-kit 进程内注册表 → `GET /metrics`。
夹具复用 `tests/agent_worker/conftest.py` 的真实租户与真实 PG 清理口径。
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import uvicorn
from conftest import TenantContext
from fastapi import FastAPI
from helpers import (
    FakeResolver,
    RecordingExecutor,
    build_resolve_response,
    create_schedule_payload,
    fetch_task,
    persist_task,
    sample_route,
)
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_agent_worker.infrastructure.models.task import TaskSchedule
from muad_agent_worker.main import app as worker_app
from muad_agent_worker.scheduler.service import SchedulerLoop, ScheduleService
from muad_agent_worker.worker.service import WorkerLoop
from muad_contracts import ScheduleSpec
from sqlalchemy import update

METRICS_PATH = "/metrics"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
LISTEN_TIMEOUT_SEC = 15.0
DELIVERIES_URL = "http://im-gateway"
SKILL_TYPE = "SKILL"
FIRED_STATUS = "FIRED"
MISFIRE_STATUS = "SCHEDULE_MISFIRE_SKIPPED"

# docs/09 §6.3 Worker 指标目录（节选，本任务落地面）
WORKER_METRIC_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("tasks_total", "counter", ("type", "status")),
    ("task_queue_depth", "gauge", ()),
    ("task_reclaim_total", "counter", ()),
    ("task_lease_expired_total", "counter", ()),
    ("scheduled_fire_total", "counter", ("status",)),
    ("scheduled_misfire_total", "counter", ()),
    ("delivery_total", "counter", ("status",)),
)

# label 卫生：label 名不得含敏感标记，label 值不得含资源 ID / 凭据形态 / 消息正文
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


async def _create_schedule(tenant: TenantContext, schedule: ScheduleSpec) -> TaskSchedule:
    payload = create_schedule_payload(tenant, schedule=schedule)
    async with tenant.session_factory() as session:
        created = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id, payload
        )
        await session.commit()
    return created


async def _set_next_fire_at(
    tenant: TenantContext, schedule_id: uuid.UUID, next_fire_at: datetime
) -> None:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=next_fire_at)
        )


async def test_b212_metrics_endpoint_exposes_worker_catalog(tenant: TenantContext) -> None:
    """B-212：真实 `/metrics` 暴露 Worker 目录，且样本 label 无敏感形状。"""
    async with _serve_http(worker_app) as http:
        samples, text = await _scrape(http)

    _assert_catalog_declared(text)
    _assert_label_hygiene(samples, text)
    assert tenant.tenant_id not in text, "指标不得落入租户标识"


async def test_b212_worker_loop_increments_task_counters(tenant: TenantContext) -> None:
    """B-212：真实 claim → 执行 → 终态 → reclaim，`tasks_total` 等计数随真实调用递增。"""
    planned = await persist_task(tenant)
    async with _serve_http(worker_app) as http:
        before, _text = await _scrape(http)
        completed_before = _sample_value(
            before, "tasks_total", {"type": SKILL_TYPE, "status": "COMPLETED"}
        ) or 0

        worker = WorkerLoop(
            tenant.session_factory,
            tenant.settings,
            executor=RecordingExecutor(),
            instance_id="b212-metrics",
        )
        assert await worker.run_once() is not None, "真实 claim 未取到已入队任务"

        now = datetime.now(UTC)
        expired = await persist_task(
            tenant,
            status="RUNNING",
            lease_owner="b212-dead",
            lease_until=now - timedelta(seconds=1),
        )
        assert await worker.reclaim_expired(now=now) >= 1
        requeued = await fetch_task(tenant, expired.id)
        assert requeued.status == "QUEUED", "过期 lease 未被回收为可再 claim"

        samples, text = await _scrape(http)

    completed_after = _sample_value(
        samples, "tasks_total", {"type": SKILL_TYPE, "status": "COMPLETED"}
    )
    assert (completed_after or 0) >= completed_before + 1, f"终态任务未计数：\n{text}"
    assert (_sample_value(samples, "task_reclaim_total", {}) or 0) >= 1, text
    assert (_sample_value(samples, "task_lease_expired_total", {}) or 0) >= 1, text
    assert _sample_value(samples, "task_queue_depth", {}) is not None, f"未采集队列深度：\n{text}"

    _assert_label_hygiene(samples, text)
    assert str(planned.id) not in text, "指标落入具体 Task ID"
    assert str(planned.tenant_id) not in text, "指标落入租户标识"


async def test_b212_schedule_and_delivery_increment_counters(tenant: TenantContext) -> None:
    """B-212：真实 Schedule 触发/错过 + 真实投递结局分别计数。"""
    now = datetime.now(UTC)
    schedule = await _create_schedule(
        tenant, ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="UTC")
    )
    misfired = await _create_schedule(
        tenant, ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="UTC")
    )
    await _set_next_fire_at(tenant, schedule.id, now - timedelta(seconds=1))
    await _set_next_fire_at(
        tenant,
        misfired.id,
        now - timedelta(seconds=tenant.settings.misfire_grace_sec + 30),
    )
    resolver = FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4()))
    loop = SchedulerLoop(tenant.session_factory, resolver, tenant.settings)

    delivered = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=now,
        delivery_route=sample_route(),
        result_json={"text": BODY_CANARY},
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    async with _serve_http(worker_app) as http:
        await loop.run_due(now=now)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            delivery = DeliveryLoop(
                tenant.session_factory,
                HttpDeliveryClient(DELIVERIES_URL, client),
                tenant.settings,
            )
            assert await delivery.run_once(now=now) is not None, "无可投递任务"
        samples, text = await _scrape(http)

    assert (
        _sample_value(samples, "scheduled_fire_total", {"status": FIRED_STATUS}) or 0
    ) >= 1, text
    assert (
        _sample_value(samples, "scheduled_fire_total", {"status": MISFIRE_STATUS}) or 0
    ) >= 1, text
    assert (_sample_value(samples, "scheduled_misfire_total", {}) or 0) >= 1, text
    assert (_sample_value(samples, "delivery_total", {"status": "SENT"}) or 0) >= 1, text

    _assert_label_hygiene(samples, text)
    assert BODY_CANARY not in text, "指标落入消息正文"
    assert str(delivered.id) not in text, "指标落入具体 Task ID"


def test_b212_declared_catalog_matches_design_and_label_names_are_hygienic() -> None:
    """B-212：服务声明的目录与设计 §3.5 逐项一致，且 label 名不含敏感标记。"""
    from muad_agent_worker.metrics import CATALOG

    declared = {name: (kind, labels) for name, kind, labels, _help in CATALOG}
    for name, kind, labels in WORKER_METRIC_CATALOG:
        assert declared.get(name) == (kind, labels), f"{name} 的声明与设计不一致：{declared.get(name)}"
        for label in labels:
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in label.lower(), f"{name} 的 label 名命中敏感标记：{label}"
