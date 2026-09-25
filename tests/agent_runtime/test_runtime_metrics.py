"""[B-212] Runtime 指标目录：真实 `/metrics` HTTP 端点 + 真实调用点递增 + label 卫生。

design 11-audit-observability §3.5 / docs/09 §6.2。
不得 Mock 的真实边界：
- 真实 uvicorn 单进程（127.0.0.1 真实 socket，非 ASGI 内存传输）+ 真实 Runtime 路由/依赖栈；
- 真实 PostgreSQL（Run/Snapshot/CanonicalEvent 落库与终态 CAS）；
- 真实 SSE 响应体（`POST /v1/runs`）→ api-kit 进程内注册表 → `GET /metrics` Prometheus 文本。
夹具复用 `tests/agent_runtime/conftest.py` 的真实租户与真实 PG 清理口径（`client` 夹具的
依赖覆盖在此重放，因为本用例要真实 socket 而非 ASGI 内存传输）。
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
from typing import Any

import httpx
import uvicorn
from conftest import TenantContext, parse_sse
from fastapi import FastAPI
from muad_agent_runtime.api.deps import (
    get_credentials_client,
    get_executor_factory,
    get_resolve_client,
)
from muad_agent_runtime.application.executor import ExecutorFactory
from muad_agent_runtime.application.run_service import reap_abandoned_runs
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
from muad_agent_runtime.main import app
from muad_contracts import ResolveDefinitionResponse

METRICS_PATH = "/metrics"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
LISTEN_TIMEOUT_SEC = 15.0
RUN_COMPLETED_EVENT = "run.completed"
COMPLETED_STATUS = "COMPLETED"

# docs/09 §6.2 Runtime 指标目录（节选，本任务落地面）
RUNTIME_METRIC_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("agent_runs_total", "counter", ("agent", "status")),
    ("model_invocations_total", "counter", ("provider", "model", "status")),
    ("tool_calls_total", "counter", ("kind", "tool", "status")),
    ("skill_load_total", "counter", ("skill", "status")),
    ("egress_calls_total", "counter", ("platform", "status")),
    ("artifact_bytes_total", "counter", ("type",)),
)

# label 卫生：label 名不得含敏感标记，label 值不得含资源 ID / 凭据形态
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
        for name, kind, _labels in RUNTIME_METRIC_CATALOG
        if f"# TYPE {name} {kind}" not in text
    ]
    assert not missing, f"Runtime 指标目录缺失：{missing}\n{text}"


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


@asynccontextmanager
async def _runtime_http(
    fake_resolve: Any, executor_factory: ExecutorFactory
) -> AsyncIterator[httpx.AsyncClient]:
    """真实 HTTP 服务真实 Runtime app；依赖覆盖与 `conftest.client` 同口径。"""
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: executor_factory
    app.dependency_overrides[get_credentials_client] = lambda: None
    try:
        async with _serve_http(app) as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_resolve_client, None)
        app.dependency_overrides.pop(get_executor_factory, None)
        app.dependency_overrides.pop(get_credentials_client, None)


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext) -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "hello metrics"},
    }


async def test_b212_metrics_endpoint_exposes_runtime_catalog(
    tenant: TenantContext, fake_resolve: Any, executor_factory: ExecutorFactory
) -> None:
    """B-212：真实 `/metrics` 暴露 Runtime 目录，且样本 label 无敏感形状。"""
    async with _runtime_http(fake_resolve, executor_factory) as http:
        response = await http.get(METRICS_PATH)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    text = response.text
    _assert_catalog_declared(text)
    _assert_label_hygiene(_parse_samples(text), text)
    assert tenant.tenant_id not in text, "指标不得落入租户标识"


async def test_b212_run_creation_increments_agent_runs_counter(
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: Any,
    executor_factory: ExecutorFactory,
) -> None:
    """B-212：真实 SSE run（真实 PG 终态 CAS）后 `agent_runs_total` 递增，label 为 Agent 键而非 ID。"""
    async with _runtime_http(fake_resolve, executor_factory) as http:
        created = await http.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
        assert created.status_code == 200, created.text
        events = parse_sse(created.text)
        assert [event["type"] for event in events][-1] == RUN_COMPLETED_EVENT, events
        run_id = uuid.UUID(events[0]["run_id"])
        response = await http.get(METRICS_PATH)

    assert response.status_code == 200, response.text
    text = response.text
    samples = _parse_samples(text)
    counter = _sample_value(
        samples, "agent_runs_total", {"agent": resolved.agent.key, "status": COMPLETED_STATUS}
    )
    assert (counter or 0) >= 1, f"真实 run 终态未计数：\n{text}"

    _assert_label_hygiene(samples, text)
    # 资源 ID（Run/会话/租户）不得作为 label 值出现
    assert str(run_id) not in text, "指标落入具体 Run ID"
    assert str(tenant.agent_id) not in text, "指标落入具体 Agent ID"
    assert str(tenant.platform_user_id) not in text, "指标落入具体用户 ID"
    assert tenant.tenant_id not in text, "指标落入租户标识"


async def _seed_expired_run(tenant: TenantContext) -> uuid.UUID:
    """落一个 lease 已过期的 RUNNING Run（真实 PG），供真实 Reaper 回收。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="RUNNING",
            input_text="abandoned",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
            lease_owner="b212-dead",
            lease_until=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_b212_run_reaper_increments_run_reclaim_counter(tenant: TenantContext) -> None:
    """B-212：真实 Reaper 回收过期 RUNNING（真实 PG CAS）后 `run_reclaim_total` 递增。"""
    abandoned = await _seed_expired_run(tenant)
    async with _serve_http(app) as http:
        assert await reap_abandoned_runs(get_session_factory()) >= 1, "Reaper 未回收过期 Run"
        response = await http.get(METRICS_PATH)

    assert response.status_code == 200, response.text
    text = response.text
    samples = _parse_samples(text)
    assert (_sample_value(samples, "run_reclaim_total", {}) or 0) >= 1, text
    _assert_label_hygiene(samples, text)
    assert str(abandoned) not in text, "指标落入具体 Run ID"


def test_b212_declared_catalog_matches_design_and_label_names_are_hygienic() -> None:
    """B-212：服务声明的目录与设计 §3.5 逐项一致，且 label 名不含敏感标记。"""
    from muad_agent_runtime.metrics import CATALOG

    declared = {name: (kind, labels) for name, kind, labels, _help in CATALOG}
    for name, kind, labels in RUNTIME_METRIC_CATALOG:
        assert declared.get(name) == (kind, labels), f"{name} 的声明与设计不一致：{declared.get(name)}"
        for label in labels:
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in label.lower(), f"{name} 的 label 名命中敏感标记：{label}"
