"""[E-02 / RULE-01 / RULE-04 / RULE-log-001 / RULE-secret-001] 三层脱敏收口（日志/写入/响应）。

不得 Mock 的真实边界：
- 审计写入（Console）：真实 `AuditService.record_config_change` → api-kit `write_config_audit` 原语
  → 真实 PostgreSQL（`control.config_audit_log` 逐行回读 + `jsonb::text LIKE` 明文探针）；
- 审计写入（Runtime）：真实 `RuntimeAuditWriter` → 真实 `runtime.tool_call_audit`；
- 日志出口：真实 `configure_logging`（仅配置 `LOG_DIR`）→ 真实日志文件；
- Console 响应：真实 Console HTTP（ASGI 全栈 + 登录会话/CSRF）→ API-02 详情响应。
验收类不制造 RED；`client`/`tenant` 夹具复用 console_platform 的既有真实租户（同库、同清理口径）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from console_platform import conftest as console_conftest
from console_platform.conftest import TenantContext
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory as runtime_session_factory
from muad_console_platform.application.audit_service import AuditActor, AuditService
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_logging import clear_log_context, configure_logging, set_log_context
from sqlalchemy import text

# 夹具复用：以原名把 console_platform 的真实夹具（真实 Console HTTP + 真实 PostgreSQL 租户清理）
# 注册到本模块；pytest 按模块属性名解析夹具，故不能用别名 import。
database_guard = console_conftest.database_guard
tenant = console_conftest.tenant
client = console_conftest.client

SERVICE = "test-audit-redaction"
MASK = "<redacted>"  # 审计写入层（runtime/api-kit 口径）的遮蔽标记
LOG_MASK = "***"  # logging-kit 的遮蔽标记

# 设计 §3.5 最低识别字段清单（docs/09 §3）：HTTP 头 + 已知敏感 key。值可辨认以便明文探针命中。
AUTHORIZATION_SECRET = "Bearer e02-authz-2f7c1d9b4a6e"
COOKIE_SECRET = "e02-session-cookie-8b1e4f7a"
SET_COOKIE_SECRET = "e02-set-cookie-6e1d4a6e"
API_KEY_SECRET = "sk-live-e02-4a6e1d9b2f7c"
ACCESS_TOKEN_SECRET = "at-e02-1d9b4a6e2f7c"
REFRESH_TOKEN_SECRET = "rt-e02-9b4a6e1d2f7c"
PASSWORD_SECRET = "e02-hunter2-pass"
CLIENT_SECRET = "cs-e02-6e1d4a6e2f7c"

SECRETS = (
    AUTHORIZATION_SECRET,
    COOKIE_SECRET,
    SET_COOKIE_SECRET,
    API_KEY_SECRET,
    ACCESS_TOKEN_SECRET,
    REFRESH_TOKEN_SECRET,
    PASSWORD_SECRET,
    CLIENT_SECRET,
)


def _secret_payload(*, revision: int) -> dict[str, Any]:
    """一份含 Secret 的配置快照：HTTP 头 + 已知敏感 key + 非敏感字段（用于证明只剔敏感）。"""
    return {
        "name": "E-02 Config",
        "revision": revision,
        "headers": {
            "Authorization": AUTHORIZATION_SECRET,
            "Cookie": COOKIE_SECRET,
            "Set-Cookie": SET_COOKIE_SECRET,
            "X-Trace": "trace-keep-me",
        },
        "api_key": API_KEY_SECRET,
        "access_token": ACCESS_TOKEN_SECRET,
        "refresh_token": REFRESH_TOKEN_SECRET,
        "password": PASSWORD_SECRET,
        "client_secret": CLIENT_SECRET,
    }


def _assert_no_plaintext(rendered: str, *, where: str) -> None:
    for secret in SECRETS:
        assert secret not in rendered, f"{where} 落入了 Secret 明文：{secret}"


async def _seed_console_config_audit(tenant: TenantContext) -> uuid.UUID:
    """真实 Console 审计写入路径（api-kit 原语，与业务共用 session），返回落库的 audit_id。"""
    session_factory = console_session_factory()
    async with session_factory() as session:
        await AuditService(session).record_config_change(
            tenant_id=tenant.tenant_id,
            actor=AuditActor(account_id=uuid.uuid4()),
            resource_type="MODEL",
            resource_id=tenant.model_id,
            action="UPDATE",
            before=_secret_payload(revision=1),
            after=_secret_payload(revision=2),
        )
        await session.commit()
    async with session_factory() as session:
        audit_id = await session.scalar(
            text(
                "SELECT id FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND resource_id = :resource_id "
                "ORDER BY create_time DESC LIMIT 1"
            ),
            {"tenant_id": tenant.tenant_id, "resource_id": tenant.model_id},
        )
    assert audit_id is not None, "配置审计未写入 control.config_audit_log"
    return audit_id


async def _config_audit_jsonb(tenant_id: str, audit_id: uuid.UUID) -> tuple[str | None, str | None]:
    async with console_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT before_json::text, after_json::text FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND id = :audit_id"
                ),
                {"tenant_id": tenant_id, "audit_id": audit_id},
            )
        ).one()
    return row[0], row[1]


async def _plaintext_hits(tenant_id: str, audit_id: uuid.UUID, secret: str) -> int:
    """`jsonb::text LIKE '%<secret>%'` 明文探针（0 表示落库值中不存在该明文）。"""
    async with console_session_factory()() as session:
        hits = await session.scalar(
            text(
                "SELECT count(*) FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND id = :audit_id "
                "AND (before_json::text LIKE :pattern OR after_json::text LIKE :pattern)"
            ),
            {
                "tenant_id": tenant_id,
                "audit_id": audit_id,
                "pattern": f"%{secret}%",
            },
        )
    return int(hits or 0)


async def test_e02_console_audit_write_masks_secrets_in_db(tenant: TenantContext) -> None:
    """E-02 写入层（DB）：config 审计 before/after 落库前递归脱敏，无明文且非敏感字段存活。"""
    audit_id = await _seed_console_config_audit(tenant)
    before_text, after_text = await _config_audit_jsonb(tenant.tenant_id, audit_id)

    for secret in SECRETS:
        assert await _plaintext_hits(tenant.tenant_id, audit_id, secret) == 0, (
            f"control.config_audit_log 中可查询到 Secret 明文：{secret}"
        )
    _assert_no_plaintext(f"{before_text}{after_text}", where="control.config_audit_log")

    before = json.loads(str(before_text))
    after = json.loads(str(after_text))
    assert before["name"] == "E-02 Config" and after["name"] == "E-02 Config"
    assert before["revision"] == 1 and after["revision"] == 2
    assert before["headers"] == {"X-Trace": "trace-keep-me"}, before["headers"]  # 嵌套结构递归脱敏


async def test_e02_runtime_audit_write_masks_args_preview_in_db(database_guard: None) -> None:
    """E-02 写入层（DB）：runtime 审计写入边界的 `args_preview_json` 落库前递归遮蔽。"""
    tenant_id = f"e02-runtime-{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4()
    try:
        writer = RuntimeAuditWriter(
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=None,
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            session_factory=runtime_session_factory,
        )
        now = datetime.now(UTC)
        await writer.record_tool_call(
            tool_call_id=f"call-{run_id.hex[:8]}",
            tool_name="http.request",
            tool_kind="SKILL",
            prepared_args_hash="sha256:e02",
            args_preview_json=_secret_payload(revision=1),
            status="OK",
            start_time=now,
            end_time=now,
            latency_ms=7,
        )
        async with runtime_session_factory()() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT args_preview_json::text, tool_name FROM runtime.tool_call_audit "
                        "WHERE tenant_id = :tenant_id AND run_id = :run_id"
                    ),
                    {"tenant_id": tenant_id, "run_id": run_id},
                )
            ).one()
        _assert_no_plaintext(str(row[0]), where="runtime.tool_call_audit.args_preview_json")
        preview = json.loads(str(row[0]))
        assert preview["api_key"] == MASK, preview["api_key"]
        assert preview["headers"]["Authorization"] == MASK, preview["headers"]
        assert preview["name"] == "E-02 Config"  # 非敏感字段存活
        assert row[1] == "http.request"
    finally:
        async with runtime_session_factory()() as session:
            await session.execute(
                text("DELETE FROM runtime.tool_call_audit WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.commit()


def _write_secret_log(service: str, log_dir: Path) -> Path:
    """真实 logging-kit 出口：仅配置 LOG_DIR，落盘 `{log_dir}/{service}/{YYYY-MM-DD}.log`。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    configure_logging(service, log_dir=log_dir, console=False)
    try:
        set_log_context(trace_id="trace-e02", request_id="req-e02")
        logger = logging.getLogger("test.audit.redaction")
        logger.info("audit_write api_key=%s", API_KEY_SECRET)
        logger.info("audit_request Authorization: %s", AUTHORIZATION_SECRET)
        logger.info("audit_payload", extra={"fields": _secret_payload(revision=1)})
    finally:
        clear_log_context()
        logging.shutdown()
        for handler in list(root.handlers):
            root.removeHandler(handler)
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return log_dir / service / f"{day}.log"


def test_e02_logging_exit_masks_secrets_in_log_file(tmp_path: Path) -> None:
    """E-02 日志出口：明文与结构化字段在写盘前遮蔽，且审计日志仍带 trace_id/request_id。"""
    service = f"{SERVICE}-{uuid.uuid4().hex[:8]}"
    log_file = _write_secret_log(service, tmp_path)
    assert log_file.exists(), "logging-kit 未按 service/日期落盘"

    content = log_file.read_text(encoding="utf-8")
    _assert_no_plaintext(content, where=f"日志文件 {log_file.name}")
    assert LOG_MASK in content, "遮蔽标记缺失（敏感字段被整条丢弃？）"
    assert "trace-keep-me" in content and "E-02 Config" in content, "非敏感字段被误伤"
    assert '"trace_id":"trace-e02"' in content and '"request_id":"req-e02"' in content


async def test_e02_console_api_detail_masks_secrets_in_response(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """E-02 响应层：API-02 详情（CONFIG）序列化后无 Secret 明文，非敏感字段仍在。"""
    audit_id = await _seed_console_config_audit(tenant)
    response = await client.get(
        f"/api/v1/audits/{audit_id}",
        params={"audit_type": "CONFIG"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0", body
    _assert_no_plaintext(response.text, where="API-02 详情响应")

    data = body["data"]
    assert data["audit_id"] == str(audit_id)
    assert data["before"]["name"] == "E-02 Config"
    assert data["before"]["headers"] == {"X-Trace": "trace-keep-me"}, data["before"]["headers"]
    assert data["after"]["revision"] == 2
