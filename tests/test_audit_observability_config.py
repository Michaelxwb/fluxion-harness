"""[B-203] 可观测性配置：Console 指标目录 + trace 关联字段 + /healthz /readyz 语义。

design 11-audit-observability §3.5 可观测性 / §4 部署与运维；docs/09 §6.1/§6.5/§4。
不得 Mock 的真实边界：
- 真实 `/metrics` HTTP 端点（真实 uvicorn 单进程 + 真实 socket + api-kit 进程内注册表）；
- 真实 Console/Worker HTTP（真实路由与中间件栈）→ 真实 PostgreSQL（`control.config_audit_log` 回读）；
- 真实日志出口（logging-kit `configure_logging` + JSON Formatter + 脱敏滤镜 → 真实日志文件）。
验收类不制造 RED；`client`/`tenant` 夹具复用 console_platform 的既有真实租户（同库、同清理口径）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from console_platform import conftest as console_conftest
from console_platform.conftest import TenantContext
from fastapi import FastAPI
from httpx import AsyncClient
from muad_agent_worker.main import app as worker_app
from muad_console_platform.infrastructure import db as console_db
from muad_console_platform.main import app as console_app
from muad_logging import configure_logging
from sqlalchemy import text

# 夹具复用：以原名把 console_platform 的真实夹具（真实 Console HTTP + 真实 PostgreSQL 租户清理）
# 注册到本模块；pytest 按模块属性名解析夹具，故不能用别名 import。
database_guard = console_conftest.database_guard
tenant = console_conftest.tenant
client = console_conftest.client

METRICS_PATH = "/metrics"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
LISTEN_TIMEOUT_SEC = 15.0

# docs/09 §6.5 Console 指标目录（只导出 OTel/监控，不进入业务 Console）
CONSOLE_METRIC_CATALOG = (
    "console_api_requests_total",
    "skill_import_total",
    "runtime_definition_resolve_total",
    "bind_total",
)

# docs/09 §6.1 trace 关联字段（缺字段必须显式置空，不得伪造）
CORRELATION_FIELDS = (
    "trace_id",
    "request_id",
    "run_id",
    "conversation_id",
    "platform_user_id",
    "agent_id",
    "snapshot_id",
    "skill_artifact_id",
    "tool_call_id",
    "task_id",
    "schedule_id",
)
# 本用例的真实请求路径（Console 配置写入）不含 Run/Task/Conversation 上下文，这些字段必须为空串。
UNSET_FIELDS = (
    "run_id",
    "conversation_id",
    "platform_user_id",
    "agent_id",
    "snapshot_id",
    "skill_artifact_id",
    "tool_call_id",
    "task_id",
    "schedule_id",
)

# label 卫生：指标文本不得出现敏感 key 名或消息正文/PII 值
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
BODY_CANARY = "b203-canary-消息正文-不得进指标"
# label 值形态探针：具体资源 ID 与凭据形态都不该出现在指标文本里
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
CREDENTIAL_PATTERN = re.compile(
    r"(bearer\s|basic\s|sk-[A-Za-z0-9]{8,}|[A-Za-z0-9+/]{40,}={0,2}$)", re.IGNORECASE
)

SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)


def _parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    """解析 Prometheus 文本样本（只认本用例断言用到的无逗号 label 取值）。"""
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


@asynccontextmanager
async def _serve_http(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口（真实 socket，非 ASGI 内存传输）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, f"{app.title} 未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as http_client:
            yield http_client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


def _agent_payload(tenant: TenantContext) -> dict[str, object]:
    return {
        "key": f"b203-{uuid.uuid4().hex[:8]}",
        "name": "B-203 Agent",
        "description": "trace correlation",
        "instructions": "You are helpful.",
        "model_id": str(tenant.model_id),
        "runtime_config": {"temperature": 0.2},
    }


async def _config_audit_trace_ids(tenant_id: str, resource_id: str) -> list[str]:
    async with console_db.get_session_factory()() as session:
        rows = await session.execute(
            text(
                "SELECT trace_id FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND resource_id = :resource_id"
            ),
            {"tenant_id": tenant_id, "resource_id": resource_id},
        )
        return [str(row[0]) for row in rows]


def _log_records(log_file: Path) -> list[dict[str, object]]:
    if not log_file.exists():
        return []
    records: list[dict[str, object]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("{"):
            records.append(json.loads(line))
    return records


def _restore_root_handlers(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)


async def _drive_metric_producing_requests(
    http: httpx.AsyncClient,
    client: AsyncClient,
    tenant: TenantContext,
    model_id: uuid.UUID,
    headers: dict[str, str],
) -> None:
    """在真实 uvicorn 进程内驱动四条真实请求路径，使 Console 指标目录被计数。"""
    # 1) 未鉴权但命中真实路由模板的 Console API 请求（path label 必须是模板，不能含具体 UUID）
    unauth = await http.get(f"/api/v1/models/{model_id}")
    assert unauth.status_code == 401, unauth.text
    # 2) 内部 bind 失败路径（未知 bot → BOT_NOT_FOUND）
    bind = await http.post(
        "/internal/channel/bind",
        headers=headers,
        json={
            "channel": "WECOM",
            "bot_id": f"b203-unknown-bot-{uuid.uuid4().hex[:8]}",
            "external_user_id": BODY_CANARY,
            "bind_code": "b203-unknown-code",
        },
    )
    assert bind.status_code >= 400, bind.text
    # 3) resolve-definition 失败路径（未知 agent → AGENT_NOT_FOUND）
    resolve = await http.post(
        "/internal/runtime/resolve-definition",
        headers=headers,
        json={
            "agent_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
    )
    assert resolve.status_code >= 400, resolve.text
    # 4) skill import 失败路径（需登录会话 → 真实 Console HTTP 全栈；同一进程注册表）
    imported = await client.post(
        "/api/v1/skills/import",
        files={"file": ("b203.zip", b"not-a-zip-package", "application/zip")},
        data={"version": "1.0.0"},
        headers=headers,
    )
    assert imported.status_code >= 400, imported.text


async def _scrape_metrics_after_real_requests(
    client: AsyncClient, tenant: TenantContext
) -> tuple[httpx.Response, uuid.UUID]:
    """真实 uvicorn 内驱动真实请求后抓取 `/metrics`，返回响应与被请求的具体资源 ID。"""
    model_id = uuid.uuid4()
    headers = {"X-Tenant-Id": tenant.tenant_id}
    async with _serve_http(console_app) as http:
        await _drive_metric_producing_requests(http, client, tenant, model_id, headers)
        response = await http.get(METRICS_PATH)
    return response, model_id


def _assert_console_catalog_exported(samples: list[tuple[str, dict[str, str], float]], text: str) -> None:
    names = {name for name, _labels, _value in samples}
    missing = [name for name in CONSOLE_METRIC_CATALOG if name not in names]
    assert not missing, f"Console 指标目录缺失：{missing}\n{text}"


def _assert_metric_failure_counters(samples: list[tuple[str, dict[str, str], float]], text: str) -> None:
    requests_by_template = _sample_value(
        samples,
        "console_api_requests_total",
        {"path": "/api/v1/models/{model_id}", "status": "401"},
    )
    assert (requests_by_template or 0) >= 1, f"路径模板计数缺失（不得用具体路径当 label）\n{text}"
    assert (_sample_value(samples, "bind_total", {"status": "BOT_NOT_FOUND"}) or 0) >= 1, text
    assert (
        _sample_value(samples, "runtime_definition_resolve_total", {"status": "AGENT_NOT_FOUND"}) or 0
    ) >= 1, text
    assert (_sample_value(samples, "skill_import_total", {"status": "SKILL_PACKAGE_INVALID"}) or 0) >= 1, text


def _assert_label_hygiene(
    samples: list[tuple[str, dict[str, str], float]], text: str, model_id: uuid.UUID
) -> None:
    """label 卫生：具体路径取值（PII 面）、消息正文、凭据形态的取值与敏感 label 名都不得出现。

    说明：`path` label 是静态路由模板（如 `/api/v1/auth/password` 是端点名，不是凭据），
    因此敏感词只作用于 label 名，label 值改判「资源 ID / 凭据形态 / 正文」三类真实泄露面。
    """
    assert str(model_id) not in text, "指标落入具体资源 ID（动态路径值不得作为 label）"
    assert BODY_CANARY not in text, "指标落入消息正文"
    for name, labels, _value in samples:
        for key, value in labels.items():
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in key.lower(), f"指标 label 名命中敏感标记 {marker}：{name}{labels}"
            assert not UUID_PATTERN.search(value), f"指标 label 值落入具体资源 ID：{name}{labels}"
            assert not CREDENTIAL_PATTERN.search(value), f"指标 label 值呈现凭据形态：{name}{labels}"


async def test_b203_metrics_endpoint_exposes_console_catalog_without_sensitive_labels(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """B-203：真实 `/metrics` 暴露 Console 指标目录，label 不含 Secret/凭据/消息正文/PII。"""
    response, model_id = await _scrape_metrics_after_real_requests(client, tenant)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    text = response.text
    samples = _parse_samples(text)
    assert samples, f"/metrics 未导出任何样本：{text}"

    _assert_console_catalog_exported(samples, text)
    _assert_metric_failure_counters(samples, text)
    _assert_label_hygiene(samples, text, model_id)


async def _create_agent_with_trace_context(
    client: AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
    trace_id: str,
    request_id: str,
    service: str,
) -> str:
    """真实 Console HTTP 创建 Agent（真实日志出口落盘），返回新建 Agent 的 id。"""
    handlers = list(logging.getLogger().handlers)
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        created = await client.post(
            "/api/v1/agents",
            json=_agent_payload(tenant),
            headers={
                "X-Tenant-Id": tenant.tenant_id,
                "X-Trace-Id": trace_id,
                "X-Request-Id": request_id,
            },
        )
        assert created.status_code == 200, created.text
        agent_id = created.json()["data"]["id"]
        assert created.json()["trace_id"] == trace_id
        assert created.json()["request_id"] == request_id
    finally:
        logging.shutdown()
        _restore_root_handlers(handlers)
    return agent_id


def _correlated_log_records(log_file: Path, trace_id: str, request_id: str) -> list[dict[str, object]]:
    correlated = [
        record
        for record in _log_records(log_file)
        if record.get("trace_id") == trace_id and record.get("request_id") == request_id
    ]
    assert correlated, "日志出口未携带与请求一致的 trace_id/request_id（无法串联）"
    return correlated


def _latest_audit_log_record(correlated: list[dict[str, object]]) -> dict[str, object]:
    audit_records = [
        record for record in correlated if "audit" in str(record.get("message", "")).lower()
    ]
    assert audit_records, f"审计写入未在日志出口留下可串联的记录：{correlated}"
    return audit_records[-1]


def _assert_record_correlates_audit_row(
    record: dict[str, object], audit_trace_ids: list[str], request_id: str
) -> None:
    """审计行（PostgreSQL）与日志记录（日志出口）经同一 trace_id 串联。"""
    assert record["trace_id"] == audit_trace_ids[0]
    assert record["request_id"] == request_id
    missing = [field for field in CORRELATION_FIELDS if field not in record]
    assert not missing, f"日志记录缺少关联字段：{missing}"
    for field in UNSET_FIELDS:
        assert record[field] == "", f"未绑定的关联字段必须显式置空而非伪造：{field}={record[field]!r}"


async def test_b203_trace_fields_are_correlated_across_audit_and_log_context(
    client: AsyncClient, tenant: TenantContext, tmp_path: Path
) -> None:
    """B-203：一次真实请求写入审计行 → 审计行的 trace_id 与日志出口的 trace_id/request_id 串联；
    未绑定的关联字段在日志中显式置空（不伪造）。"""
    trace_id = f"b203-trace-{uuid.uuid4().hex}"
    request_id = f"b203-request-{uuid.uuid4().hex}"
    service = f"muad-console-platform-b203-{uuid.uuid4().hex[:8]}"
    agent_id = await _create_agent_with_trace_context(client, tenant, tmp_path, trace_id, request_id, service)

    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    log_file = tmp_path / service / f"{day}.log"
    assert log_file.exists(), f"真实日志出口未按 service/日期落盘：{log_file}"

    audit_trace_ids = await _config_audit_trace_ids(tenant.tenant_id, agent_id)
    assert audit_trace_ids, "变更未写入 control.config_audit_log"
    assert set(audit_trace_ids) == {trace_id}, audit_trace_ids

    correlated = _correlated_log_records(log_file, trace_id, request_id)
    record = _latest_audit_log_record(correlated)
    _assert_record_correlates_audit_row(record, audit_trace_ids, request_id)


async def test_b203_healthz_and_readyz_semantics(
    database_guard: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-203：`/healthz` 不依赖外部依赖；`/readyz` 反映依赖就绪（PG 不可达 → 503）。"""
    async with _serve_http(console_app) as http:
        health = await http.get("/healthz")
        assert health.status_code == 200, health.text
        assert health.json()["data"]["status"] == "ok"
        ready = await http.get("/readyz")
        assert ready.status_code == 200, ready.text
        assert ready.json()["data"]["status"] == "ready"

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://muad:muad@127.0.0.1:1/muad")
    console_db.get_engine.cache_clear()
    try:
        async with _serve_http(console_app) as http:
            health = await http.get("/healthz")
            assert health.status_code == 200, health.text
            ready = await http.get("/readyz")
            assert ready.status_code == 503, ready.text
            assert "database" in ready.json()["data"]["failed"], ready.json()
    finally:
        monkeypatch.undo()
        console_db.get_engine.cache_clear()


async def test_b203_readyz_stays_ready_when_redis_unavailable(
    database_guard: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-203：Redis 不可用不阻塞就绪（docs/09 §4）——Worker 只把 Redis 当低延迟 hint 上报为诊断。"""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    async with _serve_http(worker_app) as http:
        health = await http.get("/healthz")
        assert health.status_code == 200, health.text
        ready = await http.get("/readyz")
    assert ready.status_code == 200, ready.text
    data = ready.json()["data"]
    assert data["status"] == "ready", data
    assert "wakeup_hint" in data, "Redis 降级状态未作为诊断详情上报"


def test_b203_trace_correlation_field_set_matches_design() -> None:
    """B-203：api-kit 暴露的关联字段集与 docs/09 §6.1 完全一致（单一来源，不得各服务自定义）。"""
    from muad_api.context import TRACE_CORRELATION_FIELDS

    assert tuple(TRACE_CORRELATION_FIELDS) == CORRELATION_FIELDS
