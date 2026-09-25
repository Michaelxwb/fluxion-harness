"""TASK-017 后端场景真实验收：S-01 / S-03 / S-05（真实 HTTP + 真实 PostgreSQL）。

不得 Mock 的真实边界：真实多进程栈（llm-probe + Console + Runtime + Worker）、真实 HTTP、
真实 PostgreSQL（自持 engine 逐行回读审计四表 / 导出任务 / 幂等表 / 运行事实）、
真实 artifact store（导出产物字节）。

场景入口：
- S-01 浏览器形态客户端**真实登录** Console（会话 cookie + CSRF）后 `GET /api/v1/audits`；
- S-03 同一真实登录会话读审计（Console 侧入口）+ Console 审计查询面的出站调用
  `GET /internal/admin/runs/{run_id}`（**内部服务身份头**，见 `_admin_run_detail` 说明）；
- S-05 真实登录后 `POST /api/v1/audits/exports`（幂等键）→ 重放 → 轮询 API-06 → 下载产物。

用例命名与文件命名全仓唯一；测试数据一律 `audit-acceptance-<uuid>` 租户，清理断言归零。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any

import httpx
import pytest
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.runtime import Artifact, RuntimeSnapshot
from muad_api.audit import write_config_audit
from muad_api.context import set_trace_context
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.audit_export_service import IDEMPOTENCY_ENDPOINT
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.acceptance.audit_observability.environment import (
    CLEANUP_TABLES,
    INTERNAL_TOKEN,
    AuditStack,
    cleanup_tenant_artifacts,
    clear_engine_caches,
    count_tenant_artifact_files,
    count_tenant_rows,
    purge_tenant,
    require,
    run_async,
    start_audit_stack,
    stop_audit_stack,
    wait_ready,
)
from tests.e2e.seed_audit import (
    ACCOUNT_PASSWORD,
    ACCOUNT_USERNAME,
    AGENT_KEY,
    AUDIT_ROW_COUNT,
    CONFIG_ACTION,
    EGRESS_OPERATION,
    EGRESS_RESULT_STATUS,
    EGRESS_TARGET,
    EGRESS_TARGET_TYPE,
    EXPORT_FORMAT,
    MODEL_API_KEY,
    MODEL_NAME,
    MODEL_STATUS,
    TENANT,
    TOOL_KIND,
    TOOL_NAME,
    TOOL_STATUS,
    TRACE_ID,
)

HTTP_TIMEOUT_SEC = 30.0
POLL_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 0.2
SEEDED_JOB_COUNT = 1

IDEMPOTENCY_HEADER = "Idempotency-Key"
DIVERGENT_TRACE_ID = f"{TRACE_ID}-divergent"
EXPORT_IDEMPOTENCY_KEY = "audit-acceptance-s05-export"

# S-03 刻意落库的"像 Secret 的值"与"原始 Prompt"标记：响应中任何一处出现即判定泄露
SNAPSHOT_SECRET = "sk-live-snapshot-secret-s03"
SNAPSHOT_RAW_PROMPT = "RAW-SYSTEM-PROMPT-MARKER-S03"
SNAPSHOT_CONTENT_HASH = "sha256:" + "b" * 64
ARTIFACT_CHECKSUM = "sha256:" + "c" * 64
ARTIFACT_MEDIA_TYPE = "application/json"
ARTIFACT_PREVIEW = "audit acceptance artifact preview"
ARTIFACT_SIZE = 2048
TIMELINE_EVENT_TYPES = ("USER_MESSAGE", "ASSISTANT_MESSAGE")
TIMELINE_PAYLOADS = ({"text": "S-03 timeline user"}, {"text": "S-03 timeline assistant"})

CONSOLE_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# 断言用显示名（与 tests/e2e/seed_audit.py 的种子一致：账号 / 平台用户 / Agent）
SEED_ACCOUNT_NAME = "Audit Acceptance Admin"
SEED_USER_NAME = "Audit Acceptance User"
SEED_AGENT_NAME = "Audit Acceptance Agent"

ENVELOPE_FIELDS = frozenset({"code", "msg", "data", "trace_id", "request_id", "timestamp"})
PAGE_FIELDS = frozenset({"items", "page", "page_size", "total"})
EXPORT_STATUS_FIELDS = frozenset(
    {"export_id", "status", "row_count", "error_code", "create_time", "update_time"}
)
# API-01 投影统一字段集 + 仓库既有 legacy 别名（TASK-003 登记：兼容 agent-management 消费方）
UNIFIED_FIELDS = frozenset(
    {
        "audit_id",
        "audit_type",
        "resource_type",
        "resource_id",
        "actor_user_id",
        "actor_name",
        "agent_id",
        "agent_name",
        "action",
        "result_status",
        "trace_id",
        "target",
        "occurred_at",
        "started_at",
        "finished_at",
        "latency_ms",
    }
)
LEGACY_FIELDS = frozenset({"id", "actor_display_name", "create_time"})
RUN_FIELDS = frozenset(
    {
        "run_id",
        "conversation_id",
        "agent_id",
        "agent_name",
        "user_id",
        "user_name",
        "status",
        "trace_id",
        "cancel_requested",
        "error_code",
        "start_time",
        "end_time",
    }
)
SNAPSHOT_FIELDS = frozenset(
    {
        "snapshot_id",
        "schema_version",
        "agent_revision",
        "model_revision",
        "prompt_template_version",
        "content_hash",
    }
)
TIMELINE_FIELDS = frozenset({"seq", "event_type", "payload", "create_time"})
TOOL_AUDIT_FIELDS = frozenset(
    {"audit_id", "tool_name", "tool_kind", "status", "latency_ms", "error_code"}
)
EGRESS_AUDIT_FIELDS = frozenset(
    {
        "audit_id",
        "target_type",
        "target",
        "operation",
        "policy_decision",
        "result_status",
        "latency_ms",
        "error_code",
    }
)
MODEL_AUDIT_FIELDS = frozenset(
    {
        "audit_id",
        "provider",
        "model",
        "attempt",
        "status",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "error_code",
    }
)
ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "artifact_type", "media_type", "size", "checksum", "preview", "create_time"}
)
DETAIL_SECTIONS = frozenset(
    {
        "run",
        "snapshot",
        "timeline",
        "tool_audits",
        "egress_audits",
        "model_invocations",
        "artifacts",
    }
)

# 响应"无 Secret"判据：字段名不得含敏感标记；`token` 仅豁免两个 token *计数* 列
SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "credential",
    "authorization",
    "cookie",
)
TOKEN_KEY_MARKER = "token"
TOKEN_SAFE_KEYS = frozenset({"input_tokens", "output_tokens"})
LEAK_MARKERS = (
    SNAPSHOT_SECRET,
    SNAPSHOT_RAW_PROMPT,
    MODEL_API_KEY,
    ACCOUNT_PASSWORD,
    INTERNAL_TOKEN,
)


@dataclass(frozen=True)
class _RunDetailFacts:
    """S-03 为种子 Run 补充的运行事实行（Snapshot / Timeline / Artifact）。"""

    snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    timeline_seqs: tuple[int, ...]


@pytest.fixture(scope="module")
def acceptance_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AuditStack]:
    """整个文件只起一次真实栈；teardown 必停进程并做租户级清理（不留孤儿/残留）。"""
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("audit-scenarios")
    stack, processes = start_audit_stack(root)
    try:
        run_async(partial(wait_ready, f"{stack.console_url}/readyz"))
        run_async(partial(wait_ready, f"{stack.runtime_url}/readyz"))
        yield stack
    finally:
        stop_audit_stack(processes)
        purge_tenant()
        cleanup_tenant_artifacts(stack.artifact_root)
        clear_engine_caches()


@asynccontextmanager
async def _own_session() -> AsyncIterator[AsyncSession]:
    """本文件自持 engine 访问真实 PostgreSQL：不复用被测服务的连接池。"""
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@asynccontextmanager
async def _console_browser(stack: AuditStack) -> AsyncIterator[httpx.AsyncClient]:
    """真实登录（真实会话 cookie + CSRF）→ 浏览器形态的真实 HTTP 客户端。"""
    async with httpx.AsyncClient(base_url=stack.console_url, timeout=HTTP_TIMEOUT_SEC) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": ACCOUNT_USERNAME, "password": ACCOUNT_PASSWORD},
            headers={"X-Tenant-Id": stack.tenant_id},
        )
        assert login.status_code == 200, login.text
        csrf = client.cookies.get(CSRF_COOKIE)
        assert csrf, "真实登录未下发 CSRF cookie（会话不可用于写请求）"
        client.headers[CSRF_HEADER] = csrf
        client.headers["X-Tenant-Id"] = stack.tenant_id
        yield client


def _assert_envelope(body: dict[str, Any]) -> None:
    assert set(body) == ENVELOPE_FIELDS, sorted(body)
    assert body["code"] == "0", body
    assert body["trace_id"], body
    assert body["request_id"], body
    assert isinstance(body["timestamp"], str) and body["timestamp"], body


async def _scalar(sql: str, **params: Any) -> int:
    async with _own_session() as session:
        value = await session.scalar(text(sql), {"t": TENANT, **params})
    return int(value or 0)


def _seeded_audit_ids(stack: AuditStack) -> set[str]:
    rows = stack.seed.rows
    return {str(rows.config), str(rows.tool), str(rows.egress), str(rows.model)}


async def _get_audits(client: httpx.AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
    response = await client.get("/api/v1/audits", params=params)
    assert response.status_code == 200, response.text
    body = dict(response.json())
    _assert_envelope(body)
    return body


def _assert_unified_fields(items: list[dict[str, Any]]) -> None:
    for item in items:
        assert set(item) == UNIFIED_FIELDS | LEGACY_FIELDS, sorted(item)
        assert item["id"] == item["audit_id"]
        assert item["create_time"] == item["occurred_at"]
        assert item["actor_display_name"] == item["actor_name"]


def _assert_console_times(items: list[dict[str, Any]]) -> None:
    """RULE-time-001：Console 时间出参统一 `YYYY-MM-DD HH:mm:ss`。"""
    for item in items:
        assert item["occurred_at"] is not None
        for field in ("occurred_at", "started_at", "finished_at"):
            value = item[field]
            assert value is None or CONSOLE_TIME_RE.match(value), (field, value)


def _assert_desc_ordering(items: list[dict[str, Any]]) -> None:
    stamps = [datetime.strptime(item["occurred_at"], "%Y-%m-%d %H:%M:%S") for item in items]
    assert stamps == sorted(stamps, reverse=True), stamps


def _assert_row_semantics(items: list[dict[str, Any]], stack: AuditStack) -> None:
    """Actor/Agent 名称按类型分流补齐：CONFIG 取账号、运行审计取平台用户 + Agent。"""
    agent_id = str(stack.seed.context.agent_id)
    by_type = {item["audit_type"]: item for item in items}
    assert by_type["CONFIG"]["actor_name"] == SEED_ACCOUNT_NAME
    assert by_type["CONFIG"]["agent_id"] is None
    assert by_type["CONFIG"]["agent_name"] is None
    for audit_type in ("TOOL", "EGRESS", "MODEL"):
        row = by_type[audit_type]
        assert row["actor_name"] == SEED_USER_NAME, audit_type
        assert row["agent_id"] == agent_id, audit_type
        assert row["agent_name"] == SEED_AGENT_NAME, audit_type


async def _assert_result_status_is_normalised() -> None:
    """写入方原始状态值并非 SUCCESS：出参的 SUCCESS 是投影归一的真实结果（RULE-08）。"""
    async with _own_session() as session:
        rows = await session.execute(
            text(
                "SELECT status FROM runtime.tool_call_audit WHERE tenant_id = :t"
                " UNION ALL SELECT result_status FROM runtime.egress_audit WHERE tenant_id = :t"
                " UNION ALL SELECT status FROM runtime.model_invocation_audit WHERE tenant_id = :t"
            ),
            {"t": TENANT},
        )
        raw = {str(value) for value in rows.scalars().all()}
    assert raw == {TOOL_STATUS, EGRESS_RESULT_STATUS, MODEL_STATUS}, raw
    assert "SUCCESS" not in raw, raw


async def _write_divergent_config_audit(stack: AuditStack) -> uuid.UUID:
    """经真实配置审计写入 port 落一条**不同 trace** 的审计行（证明列表按 trace 收窄）。"""
    context = stack.seed.context
    set_trace_context(trace_id=DIVERGENT_TRACE_ID)
    try:
        async with _own_session() as session:
            await write_config_audit(
                session,
                actor_user_id=context.account_id,
                resource_type="AGENT",
                resource_id=context.agent_id,
                action=CONFIG_ACTION,
                before={"trace": DIVERGENT_TRACE_ID},
                after={"trace": DIVERGENT_TRACE_ID},
                tenant_id=TENANT,
            )
            await session.commit()
    finally:
        set_trace_context(trace_id=TRACE_ID)
    async with _own_session() as session:
        audit_id = await session.scalar(
            text(
                "SELECT id FROM control.config_audit_log"
                " WHERE tenant_id = :t AND trace_id = :trace"
            ),
            {"t": TENANT, "trace": DIVERGENT_TRACE_ID},
        )
    assert audit_id is not None, "差异 trace 的审计行未落库"
    return uuid.UUID(str(audit_id))


async def test_s01_audit_list_returns_trace_scoped_unified_rows(
    acceptance_stack: AuditStack,
) -> None:
    stack = acceptance_stack
    divergent_id = await _write_divergent_config_audit(stack)

    async with _console_browser(stack) as client:
        body = await _get_audits(client, {"trace_id": TRACE_ID})
        page_two = await _get_audits(client, {"trace_id": TRACE_ID, "page": 2, "page_size": 3})

    data = body["data"]
    assert set(data) == PAGE_FIELDS, sorted(data)
    assert (data["page"], data["page_size"], data["total"]) == (1, 20, AUDIT_ROW_COUNT)
    items = data["items"]
    assert {item["audit_type"] for item in items} == {"CONFIG", "TOOL", "EGRESS", "MODEL"}
    assert {item["audit_id"] for item in items} == _seeded_audit_ids(stack)
    # 不同 trace 的审计行真实存在，但不得进入本次结果
    assert str(divergent_id) not in {item["audit_id"] for item in items}
    assert {item["trace_id"] for item in items} == {TRACE_ID}
    _assert_unified_fields(items)
    _assert_console_times(items)
    _assert_desc_ordering(items)
    _assert_row_semantics(items, stack)
    assert {item["result_status"] for item in items} == {"SUCCESS"}
    await _assert_result_status_is_normalised()

    second = page_two["data"]
    assert set(second) == PAGE_FIELDS, sorted(second)
    assert (second["page"], second["page_size"], second["total"]) == (2, 3, AUDIT_ROW_COUNT)
    assert len(second["items"]) == 1
    assert {item["audit_id"] for item in second["items"]} <= _seeded_audit_ids(stack)


def _snapshot_row(stack: AuditStack, snapshot_id: uuid.UUID) -> RuntimeSnapshot:
    """冻结快照真实行：`model_json` 含模型密钥、`agent_json` 含原始指令，端点只能投影摘要。"""
    return RuntimeSnapshot(
        id=snapshot_id,
        tenant_id=TENANT,
        run_id=stack.seed.context.run_id,
        schema_version=1,
        agent_revision=3,
        model_revision=2,
        agent_json={"key": AGENT_KEY, "instructions": SNAPSHOT_RAW_PROMPT},
        model_json={"model_id": MODEL_NAME, "api_key": SNAPSHOT_SECRET},
        skill_catalog_json=[],
        mcp_catalog_json=[],
        policy_json={},
        prompt_template_version="1",
        content_hash=SNAPSHOT_CONTENT_HASH,
    )


def _artifact_row(stack: AuditStack, artifact_id: uuid.UUID) -> Artifact:
    return Artifact(
        id=artifact_id,
        tenant_id=TENANT,
        run_id=stack.seed.context.run_id,
        conversation_id=stack.seed.context.conversation_id,
        artifact_type="TOOL_RESULT",
        storage_key=f"artifacts/{artifact_id}/result.json",
        media_type=ARTIFACT_MEDIA_TYPE,
        size=ARTIFACT_SIZE,
        checksum=ARTIFACT_CHECKSUM,
        preview_text=ARTIFACT_PREVIEW,
    )


async def _seed_run_detail_facts(stack: AuditStack) -> _RunDetailFacts:
    """为种子 Run 补 Snapshot/Timeline/Artifact 真实行（Timeline 走真实 EventWriter）。"""
    context = stack.seed.context
    snapshot_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    async with _own_session() as session:
        session.add(_snapshot_row(stack, snapshot_id))
        writer = EventWriter(session)
        seqs: list[int] = []
        for event_type, payload in zip(TIMELINE_EVENT_TYPES, TIMELINE_PAYLOADS, strict=True):
            seqs.append(
                await writer.append(
                    tenant_id=TENANT,
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    event_type=event_type,
                    payload=payload,
                )
            )
        session.add(_artifact_row(stack, artifact_id))
        await session.commit()
    return _RunDetailFacts(
        snapshot_id=snapshot_id, artifact_id=artifact_id, timeline_seqs=tuple(seqs)
    )


async def _admin_run_detail(stack: AuditStack, *, with_identity: bool = True) -> httpx.Response:
    """Console 审计查询面的出站调用：Runtime 内部端点。

    设计把 API-03/04 定为"Console 审计查询面（后端出站调用）"，Console 侧不提供浏览器直连
    的内部端点路由，故本用例以**与 Console 客户端完全一致的头部契约**（`X-Internal-Service`
    服务身份 + 认证租户）发起该出站调用；`with_identity=False` 用于验证身份边界确实生效。
    """
    headers = stack.service_headers() if with_identity else {"X-Tenant-Id": stack.tenant_id}
    async with httpx.AsyncClient(base_url=stack.runtime_url, timeout=HTTP_TIMEOUT_SEC) as client:
        return await client.get(
            f"/internal/admin/runs/{stack.seed.context.run_id}", headers=headers
        )


def _assert_run_section(run: dict[str, Any], stack: AuditStack) -> None:
    context = stack.seed.context
    assert set(run) == RUN_FIELDS, sorted(run)
    assert run["run_id"] == str(context.run_id)
    assert run["conversation_id"] == str(context.conversation_id)
    assert run["agent_id"] == str(context.agent_id)
    assert run["agent_name"] == SEED_AGENT_NAME
    assert run["user_id"] == str(context.user_id)
    assert run["user_name"] == SEED_USER_NAME
    assert run["status"] == "COMPLETED"
    assert run["trace_id"] == context.trace_id == TRACE_ID
    assert run["cancel_requested"] is False
    assert run["error_code"] is None
    for field in ("start_time", "end_time"):
        assert datetime.fromisoformat(run[field]).tzinfo is not None, field


def _assert_snapshot_section(snapshot: dict[str, Any] | None, facts: _RunDetailFacts) -> None:
    assert snapshot is not None, "Snapshot 摘要缺失"
    assert set(snapshot) == SNAPSHOT_FIELDS, sorted(snapshot)
    assert snapshot["snapshot_id"] == str(facts.snapshot_id)
    assert snapshot["schema_version"] == 1
    assert snapshot["agent_revision"] == 3
    assert snapshot["model_revision"] == 2
    assert snapshot["prompt_template_version"] == "1"
    assert snapshot["content_hash"] == SNAPSHOT_CONTENT_HASH


def _assert_timeline_section(timeline: list[dict[str, Any]], facts: _RunDetailFacts) -> None:
    assert [row["seq"] for row in timeline] == list(facts.timeline_seqs), timeline
    assert [row["event_type"] for row in timeline] == list(TIMELINE_EVENT_TYPES)
    assert [row["payload"] for row in timeline] == list(TIMELINE_PAYLOADS)
    for row in timeline:
        assert set(row) == TIMELINE_FIELDS, sorted(row)
        assert datetime.fromisoformat(row["create_time"]).tzinfo is not None


def _assert_audit_sections(detail: dict[str, Any], stack: AuditStack) -> None:
    """三段运行审计与四表投影同源（audit_id 为种子真实行 id）。"""
    rows = stack.seed.rows
    tools = detail["tool_audits"]
    assert len(tools) == 1 and set(tools[0]) == TOOL_AUDIT_FIELDS, tools
    assert tools[0] == {
        "audit_id": str(rows.tool),
        "tool_name": TOOL_NAME,
        "tool_kind": TOOL_KIND,
        "status": TOOL_STATUS,
        "latency_ms": 12,
        "error_code": None,
    }
    egress = detail["egress_audits"]
    assert len(egress) == 1 and set(egress[0]) == EGRESS_AUDIT_FIELDS, egress
    assert egress[0] == {
        "audit_id": str(rows.egress),
        "target_type": EGRESS_TARGET_TYPE,
        "target": EGRESS_TARGET,
        "operation": EGRESS_OPERATION,
        "policy_decision": "ALLOW",
        "result_status": EGRESS_RESULT_STATUS,
        "latency_ms": 34,
        "error_code": None,
    }
    models = detail["model_invocations"]
    assert len(models) == 1 and set(models[0]) == MODEL_AUDIT_FIELDS, models
    assert models[0] == {
        "audit_id": str(rows.model),
        "provider": "openai",
        "model": MODEL_NAME,
        "attempt": 1,
        "status": MODEL_STATUS,
        "input_tokens": 8,
        "output_tokens": 3,
        "latency_ms": 56,
        "error_code": None,
    }


def _assert_artifact_section(artifacts: list[dict[str, Any]], facts: _RunDetailFacts) -> None:
    assert len(artifacts) == 1, artifacts
    row = artifacts[0]
    assert set(row) == ARTIFACT_FIELDS, sorted(row)
    assert row["artifact_id"] == str(facts.artifact_id)
    assert row["artifact_type"] == "TOOL_RESULT"
    assert row["media_type"] == ARTIFACT_MEDIA_TYPE
    assert row["size"] == ARTIFACT_SIZE
    assert row["checksum"] == ARTIFACT_CHECKSUM
    assert row["preview"] == ARTIFACT_PREVIEW
    assert datetime.fromisoformat(row["create_time"]).tzinfo is not None


def _iter_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def _assert_no_secret(raw_text: str, body: dict[str, Any]) -> None:
    """序列化响应无 Secret/凭据：原文字面量扫描 + 字段名敏感标记扫描。"""
    for marker in LEAK_MARKERS:
        assert marker not in raw_text, marker
    for key in _iter_keys(body):
        lowered = key.lower()
        for marker in SENSITIVE_KEY_MARKERS:
            assert marker not in lowered, key
        if TOKEN_KEY_MARKER in lowered:
            assert key in TOKEN_SAFE_KEYS, key


async def _assert_snapshot_secrets_really_exist() -> None:
    """反查落库事实：证明上一条"无泄露"断言不是空过（快照内确有密钥与原始 Prompt）。"""
    async with _own_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT model_json->>'api_key' AS api_key,"
                    " agent_json->>'instructions' AS instructions"
                    " FROM runtime.runtime_snapshot WHERE tenant_id = :t"
                ),
                {"t": TENANT},
            )
        ).one()
    assert row.api_key == SNAPSHOT_SECRET
    assert row.instructions == SNAPSHOT_RAW_PROMPT


async def test_s03_admin_run_detail_contract_exposes_no_secret(
    acceptance_stack: AuditStack,
) -> None:
    stack = acceptance_stack
    facts = await _seed_run_detail_facts(stack)

    async with _console_browser(stack) as client:
        # 浏览器腿：真实登录会话可读同 trace 的审计（S-03 的 Console 侧入口）
        audits = await _get_audits(client, {"trace_id": TRACE_ID})
    assert audits["data"]["total"] == AUDIT_ROW_COUNT

    forbidden = await _admin_run_detail(stack, with_identity=False)
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "FORBIDDEN", forbidden.text

    response = await _admin_run_detail(stack)
    assert response.status_code == 200, response.text
    body = dict(response.json())
    _assert_envelope(body)
    detail = body["data"]
    assert set(detail) == DETAIL_SECTIONS, sorted(detail)
    _assert_run_section(detail["run"], stack)
    _assert_snapshot_section(detail["snapshot"], facts)
    _assert_timeline_section(detail["timeline"], facts)
    _assert_audit_sections(detail, stack)
    _assert_artifact_section(detail["artifacts"], facts)
    await _assert_snapshot_secrets_really_exist()
    _assert_no_secret(response.text, body)


async def _create_export(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    """API-05 创建（必携幂等键）；返回响应封套。"""
    response = await client.post(
        "/api/v1/audits/exports",
        json=payload,
        headers={IDEMPOTENCY_HEADER: EXPORT_IDEMPOTENCY_KEY},
    )
    assert response.status_code == 200, response.text
    body = dict(response.json())
    _assert_envelope(body)
    return body


async def _poll_export(client: httpx.AsyncClient, export_id: str) -> dict[str, Any]:
    """轮询 API-06：状态查询内惰性执行导出，直到终态（超时失败，不静默放行）。"""
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/audits/exports/{export_id}")
        assert response.status_code == 200, response.text
        body = dict(response.json())
        _assert_envelope(body)
        last = dict(body["data"])
        if last["status"] in {"SUCCEEDED", "FAILED"}:
            return last
        await asyncio.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"导出未在 {POLL_TIMEOUT_SEC}s 内到达终态：{last}")


async def _count_export_jobs() -> int:
    return await _scalar("SELECT count(*) FROM control.audit_export_job WHERE tenant_id = :t")


async def _count_idempotency_rows() -> int:
    return await _scalar(
        "SELECT count(*) FROM control.skill_import_idempotency"
        " WHERE tenant_id = :t AND endpoint = :endpoint",
        endpoint=IDEMPOTENCY_ENDPOINT,
    )


async def _export_artifact_ref(export_id: str) -> str:
    async with _own_session() as session:
        ref = await session.scalar(
            text(
                "SELECT artifact_ref FROM control.audit_export_job"
                " WHERE tenant_id = :t AND id = :id"
            ),
            {"t": TENANT, "id": uuid.UUID(export_id)},
        )
    assert ref, "导出任务缺少 artifact_ref"
    return str(ref)


async def _export_job_status(export_id: str) -> str:
    async with _own_session() as session:
        status = await session.scalar(
            text(
                "SELECT status FROM control.audit_export_job WHERE tenant_id = :t AND id = :id"
            ),
            {"t": TENANT, "id": uuid.UUID(export_id)},
        )
    assert status, "导出任务不存在"
    return str(status)


def _assert_export_rows(rows: list[dict[str, Any]], stack: AuditStack) -> None:
    """产物内容 = 筛选命中的四表投影行，恰好 4 条（列集为统一字段，无 legacy 别名）。"""
    assert len(rows) == AUDIT_ROW_COUNT, rows
    assert {row["audit_id"] for row in rows} == _seeded_audit_ids(stack)
    assert {row["audit_type"] for row in rows} == {"CONFIG", "TOOL", "EGRESS", "MODEL"}
    assert {row["trace_id"] for row in rows} == {TRACE_ID}
    for row in rows:
        assert set(row) == UNIFIED_FIELDS, sorted(row)
        assert CONSOLE_TIME_RE.match(row["occurred_at"]), row["occurred_at"]


async def test_s05_export_creation_is_idempotent_and_downloadable(
    acceptance_stack: AuditStack,
) -> None:
    stack = acceptance_stack
    payload = {"export_format": EXPORT_FORMAT, "trace_id": TRACE_ID}

    async with _console_browser(stack) as client:
        created = await _create_export(client, payload)
        replayed = await _create_export(client, payload)
        assert replayed["data"] == created["data"], (created["data"], replayed["data"])
        export_id = str(created["data"]["export_id"])
        assert created["data"]["status"] == "PENDING"
        # 同 key 同指纹重放首次结果：不产生第二个任务，也不重复记录幂等行
        assert await _count_export_jobs() == SEEDED_JOB_COUNT + 1
        assert await _count_idempotency_rows() == SEEDED_JOB_COUNT + 1
        # 创建只落 PENDING：执行由 API-06 状态查询惰性驱动（真实状态迁移）
        assert await _export_job_status(export_id) == "PENDING"

        status = await _poll_export(client, export_id)
        download = await client.get(f"/api/v1/audits/exports/{export_id}/download")

    assert set(status) == EXPORT_STATUS_FIELDS, sorted(status)
    assert status["export_id"] == export_id
    assert status["status"] == "SUCCEEDED", status
    assert status["row_count"] == AUDIT_ROW_COUNT
    assert status["error_code"] is None

    assert download.status_code == 200, download.text
    assert download.headers["content-type"].split(";")[0] == ARTIFACT_MEDIA_TYPE
    assert download.headers["content-disposition"] == (
        f'attachment; filename="audits-{export_id}.json"'
    )
    rows = json.loads(download.content)
    _assert_export_rows(rows, stack)
    # 下载字节与 artifact store 真实产物逐字节一致（不经第二套序列化）
    artifact_ref = await _export_artifact_ref(export_id)
    assert (stack.artifact_root / artifact_ref).read_bytes() == download.content


def test_s05_cleanup_purges_tenant_and_artifacts(acceptance_stack: AuditStack) -> None:
    """场景收尾：租户审计数据与导出任务归零、导出产物目录消失（幂等）。

    必须保持为本文件**最后一个**用例：它清空本模块租户的全部数据（供清理断言取证）。
    """
    stack = acceptance_stack
    purge_tenant()
    residue = {table: count_tenant_rows(table) for table in CLEANUP_TABLES}
    print("S-05 清理后租户残留: " + json.dumps(residue, ensure_ascii=False))
    assert set(residue.values()) == {0}, residue
    cleanup_tenant_artifacts(stack.artifact_root)
    assert count_tenant_artifact_files(stack.artifact_root) == 0
