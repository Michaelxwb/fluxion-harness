"""[B-245] Token 签发与交换全链路脱敏（design §3.4 API-03/API-04、§3.5；TASK-045）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务/HTTP 依赖**：ASGI 直连 `muad_console_platform.main.app`——真实 ADMIN
  登录会话/CSRF、真实 API-03 签发（PUT）与 API-04 交换路由、真实 api-kit 封套与错误目录；
- **真实 PostgreSQL 与事务**：`control.chat_access_token` / `chat_session` / `config_audit_log`
  逐行回读；canary 反查扫 `control` 全库（31 张真实表的全列 `to_jsonb(row)::text`，覆盖
  Canonical 权威行、审计行、审计快照与幂等回执表）；
- **真实脱敏原语**：logging-kit `redact_text`/`redact_value` 与 api-kit `sanitize_audit_payload`——
  审计**丢键**与日志**替换**两种语义分别钉住，不互相代替；
- **真实日志出口**：真实 `configure_logging` 落盘文件 + 真实 `RedactionFilter` + `TextLogFormatter`；
- 本路径不发起任何外部模型调用，故"外部模型真实 HTTP 探针"边界在此不适用。

明文卫生主张：用户 Token 与 CHAT 会话原文只出现在两种真实传输面（签发响应体一次 / HttpOnly
Cookie 头），其余全链路——交换响应体、非 Cookie 响应头、审计行（含摘要）、结构化日志、
库内任何列——零命中。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含 CHAT 新表；本文件自带按 `tenant_id`
的清理（会话→Token→用户→设置→回执→审计，顺序满足外键），tenant 每用例唯一。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from httpx import Response as HttpResponse
from muad_api import sanitize_audit_payload
from muad_console_platform.api.chat_security import CHAT_CSRF_COOKIE, CHAT_SESSION_COOKIE
from muad_console_platform.api.security import CSRF_HEADER
from muad_console_platform.application.audit_service import AuditActor, AuditService
from muad_console_platform.application.chat_token_service import ChatTokenService, hash_chat_token
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ChatAccessToken, ChatSession
from muad_console_platform.infrastructure.models.control import ConfigAuditLog, PlatformUser
from muad_logging import configure_logging
from muad_logging.redaction import RedactionFilter, redact_text, redact_value
from sqlalchemy import select, text

from console_auth.conftest import (
    ADMIN_PASSWORD,
    AuthContext,
    csrf_headers,
    login,
    tenant_headers,
)

pytestmark = pytest.mark.integration

EXCHANGE_PATH = "/api/v1/webchat/auth/exchange"
ISSUE_PATH = "/api/v1/users/{user_id}/webchat-token"
#: 与真实 Console 应用共用同一 Host（httpx `base_url=http://test`）。
SAME_ORIGIN = "http://test"
AUDIT_RESOURCE_TYPE = "CHAT_ACCESS_TOKEN"
PURGE_STATEMENTS = (
    "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
    "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
    "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting_idempotency WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class RedactionScope:
    """本用例租户里的平台用户：签发/交换的真实凭据主体。"""

    tenant_id: str
    user_id: uuid.UUID


def _canary(kind: str) -> str:
    """一次性 canary：每次运行随机，落库/落盘后可按明文反查。"""
    return f"b245-canary-{kind}-{uuid.uuid4().hex}"


@pytest.fixture
async def redaction_scope(auth: AuthContext) -> AsyncIterator[RedactionScope]:
    factory = get_session_factory()
    async with factory() as session:
        user = PlatformUser(
            tenant_id=auth.tenant_id,
            user_code=f"u-{uuid.uuid4().hex[:8]}",
            display_name="Redaction User",
        )
        session.add(user)
        await session.commit()
        scope = RedactionScope(tenant_id=auth.tenant_id, user_id=user.id)
    try:
        yield scope
    finally:
        async with factory() as session:
            for statement in PURGE_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": auth.tenant_id})
            await session.commit()


# --- 真实服务/库内回读 -----------------------------------------------------------------


async def _issue_via_service(scope: RedactionScope) -> str:
    """真实 `ChatTokenService` 首签：拿到可交换的用户 Token 原文（本返回值是唯一明文出口）。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            expected_revision=0,
            actor=AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.7"),
        )
        await session.commit()
        return issued.token.get_secret_value()


async def _token_rows(tenant_id: str) -> list[ChatAccessToken]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .order_by(ChatAccessToken.revision)
        )
        return list(rows)


async def _session_rows(tenant_id: str) -> list[ChatSession]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatSession).where(ChatSession.tenant_id == tenant_id)
        )
        return list(rows)


async def _audit_rows(tenant_id: str) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ConfigAuditLog)
            .where(ConfigAuditLog.tenant_id == tenant_id)
            .order_by(ConfigAuditLog.create_time)
        )
        return list(rows)


async def _control_tables() -> list[str]:
    """Console Owner 库的全部真实表（canary 反查的范围就是这一整个 schema）。"""
    async with get_session_factory()() as session:
        names = await session.scalars(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'control' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        )
        return [str(name) for name in names]


async def _canary_hits(canary: str) -> dict[str, int]:
    """`control` 全库反查：命中 canary 明文的表 → 行数（空 dict 表示零命中）。

    表名来自 `information_schema`（非用户输入）；值一律走绑定参数。
    """
    hits: dict[str, int] = {}
    async with get_session_factory()() as session:
        for table in await _control_tables():
            count = await session.scalar(
                text(
                    f'SELECT count(*) FROM control."{table}" AS row_source '
                    "WHERE to_jsonb(row_source.*)::text LIKE :pattern"
                ),
                {"pattern": f"%{canary}%"},
            )
            if count:
                hits[table] = int(count)
    return hits


def _row_dump(row: Any) -> str:
    """整行（含摘要列）的文本快照：用于断言原文不在库内任何列。"""
    return json.dumps({column.key: str(getattr(row, column.key)) for column in row.__table__.columns})


def _audit_dump(rows: list[ConfigAuditLog]) -> str:
    return json.dumps(
        [{"action": row.action, "before": row.before_json, "after": row.after_json} for row in rows],
        default=str,
    )


def _log_dump(records: list[logging.LogRecord]) -> str:
    return json.dumps([{key: str(value) for key, value in record.__dict__.items()} for record in records])


def _non_cookie_headers(response: HttpResponse) -> str:
    """除 `Set-Cookie` 外的响应头：会话原文只允许出现在下发给浏览器的 Cookie 里。"""
    return json.dumps(
        [(name, value) for name, value in response.headers.items() if name.lower() != "set-cookie"]
    )


# --- B-245：敏感键清单覆盖 CHAT 新凭据形态 ------------------------------------------------


def test_b245_sensitive_key_list_covers_chat_cookie_and_token_forms() -> None:
    """[B-245] 既有敏感键清单必须覆盖签发/交换的新形态。

    真实边界：真实 logging-kit 脱敏原语（`redact_text`/`redact_value`）。
    关键断言：Cookie 头（含**第二对起**）、Set-Cookie、裸键值对、交换请求体 `token`、
    签发响应体、Bearer 与 CHAT CSRF 头全部遮蔽，非敏感字段存活。
    """
    session_canary = _canary("session")
    csrf_canary = _canary("csrf")
    token_canary = _canary("token")

    # Cookie 头：`cookie` 前缀键只吃掉第一对，第二对必须按**名字**入清单才不会原文照落。
    cookie_header = redact_text(
        f"Cookie: {CHAT_SESSION_COOKIE}={session_canary}; {CHAT_CSRF_COOKIE}={csrf_canary}"
    )
    assert session_canary not in cookie_header, cookie_header
    assert csrf_canary not in cookie_header, cookie_header
    assert cookie_header.count("***") == 2, cookie_header

    # Set-Cookie：交换响应写 Cookie 的真实形态（属性段不误伤）。
    set_cookie = redact_text(
        f"Set-Cookie: {CHAT_SESSION_COOKIE}={session_canary}; Path=/; HttpOnly; SameSite=strict"
    )
    assert session_canary not in set_cookie, set_cookie
    assert "Path=/" in set_cookie and "HttpOnly" in set_cookie, set_cookie

    # 裸键值对（无 `cookie` 前缀键）：交换请求体被文本化后的样子。
    bare_pair = redact_text(f"{CHAT_SESSION_COOKIE}={session_canary}")
    assert session_canary not in bare_pair, bare_pair

    # 交换请求体/签发响应体（结构化）：`token` 键与两枚 CHAT Cookie 名都要遮蔽。
    payload = {
        "token": token_canary,
        CHAT_SESSION_COOKIE: session_canary,
        CHAT_CSRF_COOKIE: csrf_canary,
        "display_name": "visible",
    }
    redacted = redact_value(payload)
    assert redacted["token"] == "***", redacted
    assert redacted[CHAT_SESSION_COOKIE] == "***", redacted
    assert redacted[CHAT_CSRF_COOKIE] == "***", redacted
    assert redacted["display_name"] == "visible", redacted
    rendered = json.dumps(redacted)
    assert session_canary not in rendered and csrf_canary not in rendered and token_canary not in rendered

    # 认证头：Bearer（Token 走管理/交换面时的真实形态）与独立 CHAT CSRF 头。
    bearer = redact_text(f"Authorization: Bearer {token_canary}")
    assert token_canary not in bearer, bearer
    csrf_header = redact_text(f"{CSRF_HEADER}: {csrf_canary}")
    assert csrf_canary not in csrf_header, csrf_header


def test_b245_audit_projection_drops_keys_while_log_redaction_replaces_values() -> None:
    """[B-245] 两种语义分别验证：审计**丢键**（键整条消失）、日志**替换**（键保留、值变 `***`）。

    真实边界：真实 api-kit `sanitize_audit_payload` 与真实 logging-kit `redact_value`/
    `RedactionFilter`；两者不得互相代替（丢键的载荷若原样进日志就少一层，反之亦然）。
    """
    token_canary = _canary("token")
    session_canary = _canary("session")
    csrf_canary = _canary("csrf")
    payload = {
        "name": "keep-me",
        "token": token_canary,
        CHAT_SESSION_COOKIE: session_canary,
        "nested": {CHAT_CSRF_COOKIE: csrf_canary, "revision": 2},
    }

    projected = sanitize_audit_payload(payload)
    assert projected == {"name": "keep-me", "nested": {"revision": 2}}, projected

    replaced = redact_value(payload)
    assert set(replaced) == {"name", "token", CHAT_SESSION_COOKIE, "nested"}, replaced
    assert replaced["token"] == "***" and replaced[CHAT_SESSION_COOKIE] == "***", replaced
    assert replaced["nested"] == {CHAT_CSRF_COOKIE: "***", "revision": 2}, replaced
    assert replaced["name"] == "keep-me", replaced

    # 日志出口的结构化字段（`extra={...}` 挂成 record 属性）：按键名替换而不是整条丢弃。
    record = logging.LogRecord(
        name="test.b245.redaction",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="issue_response",
        args=(),
        exc_info=None,
    )
    record.__dict__[CHAT_SESSION_COOKIE] = session_canary
    record.__dict__["revision"] = 1
    assert RedactionFilter().filter(record) is True
    assert record.__dict__[CHAT_SESSION_COOKIE] == "***", record.__dict__
    assert record.__dict__["revision"] == 1, record.__dict__


def test_b245_three_sensitive_key_lists_agree_on_the_chat_cookie_names() -> None:
    """[B-245] design §3.5 / harness-secret 约定"新增敏感键必须三处同改"。

    真实边界：三个真实包各自的真实原语——api-kit `sanitize_audit_payload`（丢键）、
    logging-kit `redact_value`（替换）、runtime `redact_sensitive`（审计遮蔽）——
    同时覆盖两枚 CHAT Cookie 名，缺一处即漏一层（不是对源码文本的正则扫描）。
    """
    from muad_agent_runtime.infrastructure.audit_writer import redact_sensitive

    session_canary = _canary("triple-session")
    csrf_canary = _canary("triple-csrf")
    payload = {CHAT_SESSION_COOKIE: session_canary, CHAT_CSRF_COOKIE: csrf_canary, "keep": 1}

    assert sanitize_audit_payload(payload) == {"keep": 1}, "api-kit 审计侧必须丢键"
    assert redact_value(payload)[CHAT_SESSION_COOKIE] == "***", "logging-kit 日志侧必须替换"
    runtime_masked = redact_sensitive(payload)
    assert runtime_masked["keep"] == 1, runtime_masked
    for name in (CHAT_SESSION_COOKIE, CHAT_CSRF_COOKIE):
        assert runtime_masked[name] == "<redacted>", runtime_masked


# --- B-245：真实签发/交换路径的明文卫生 ----------------------------------------------------


async def test_b245_issue_and_exchange_leave_no_plaintext_in_response_audit_or_logs(
    client: AsyncClient,
    auth: AuthContext,
    redaction_scope: RedactionScope,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """[B-245] 真实 API-03 签发 + API-04 交换：明文只在签发响应出现一次，其余链路零命中。

    真实边界：真实 Console ASGI（ADMIN 登录会话/CSRF）+ 真实 PostgreSQL 行回读 + 真实日志出口。
    关键断言：签发响应计数、交换响应体/非 Cookie 头、库内行、审计行（含摘要）、应用日志。
    """
    caplog.set_level(logging.INFO)
    scope = redaction_scope
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200

    issued = await client.put(
        ISSUE_PATH.format(user_id=scope.user_id),
        json={"expected_revision": 0},
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert issued.status_code == 200, issued.text
    user_token = issued.json()["data"]["token"]
    assert issued.text.count(user_token) == 1, "签发响应是原文唯一一次出现处（非空断言）"
    assert issued.headers["cache-control"] == "no-store"

    exchanged = await client.post(
        EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": SAME_ORIGIN}
    )
    assert exchanged.status_code == 200, exchanged.text
    session_token = client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token, "会话原文必须落在 HttpOnly CHAT Cookie 这一真实传输面"

    token_rows = await _token_rows(scope.tenant_id)
    session_rows = await _session_rows(scope.tenant_id)
    assert len(token_rows) == 1 and len(session_rows) == 1, "真实行存在，反查才有意义"
    assert token_rows[0].token_hash == hash_chat_token(user_token), "库里只存 SHA-256 摘要"
    assert session_rows[0].token_hash == hash_chat_token(session_token)

    metadata = await client.get(
        ISSUE_PATH.format(user_id=scope.user_id), headers=tenant_headers(auth)
    )
    assert metadata.status_code == 200
    replay = await client.put(
        ISSUE_PATH.format(user_id=scope.user_id),
        json={"expected_revision": 0},
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert replay.status_code == 409 and replay.json()["code"] == "REVISION_CONFLICT"

    rendered = {
        "exchange_body": exchanged.text,
        "exchange_non_cookie_headers": _non_cookie_headers(exchanged),
        "metadata_body": metadata.text,
        "replay_body": replay.text,
        "token_row": _row_dump(token_rows[0]),
        "session_row": _row_dump(session_rows[0]),
        "audit_rows": _audit_dump(await _audit_rows(scope.tenant_id)),
        "app_logs": _log_dump(caplog.records),
    }
    for where, payload in rendered.items():
        for secret in (user_token, session_token):
            assert secret not in payload, f"{where} 落入凭据明文"
    digest = hash_chat_token(user_token)
    assert digest not in rendered["audit_rows"], "审计连摘要都不写"
    assert digest not in rendered["exchange_body"] and digest not in rendered["metadata_body"]


# --- B-245：日志出口（真实落盘） ----------------------------------------------------------


def test_b245_logging_exit_replaces_chat_credentials_in_the_log_file(tmp_path: Path) -> None:
    """[B-245] 真实日志出口：Cookie 头/Bearer/交换体落盘前全部替换为 `***`，非敏感字段存活。

    真实边界：真实 `configure_logging`（仅配置 `LOG_DIR`）+ 真实 `RedactionFilter` 与
    `TextLogFormatter`；断言直接读落盘文件，不依赖内存记录。
    """
    service = f"test-b245-redaction-{uuid.uuid4().hex[:8]}"
    session_canary = _canary("session")
    csrf_canary = _canary("csrf")
    token_canary = _canary("token")
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        logger = logging.getLogger("test.b245.redaction")
        logger.info(
            "exchange_request Cookie: %s=%s; %s=%s",
            CHAT_SESSION_COOKIE,
            session_canary,
            CHAT_CSRF_COOKIE,
            csrf_canary,
        )
        logger.info("issue_response Authorization: Bearer %s", token_canary)
        logger.info(
            "exchange_payload",
            extra={
                "fields": {
                    "token": token_canary,
                    CHAT_SESSION_COOKIE: session_canary,
                    "user": "keep-me",
                }
            },
        )
    finally:
        logging.shutdown()
        for handler in list(root.handlers):
            root.removeHandler(handler)

    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    content = (tmp_path / service / f"{day}.log").read_text(encoding="utf-8")
    for canary in (session_canary, csrf_canary, token_canary):
        assert canary not in content, f"日志文件落入凭据明文：{canary}"
    assert "***" in content, "遮蔽标记缺失（敏感字段被整条丢弃？）"
    assert "keep-me" in content, "非敏感字段被误伤（替换语义而非整条丢弃）"


# --- B-245：canary 反查（Canonical/审计/快照/幂等回执） ------------------------------------


async def test_b245_canary_reverse_lookup_finds_no_plaintext_in_console_storage(
    client: AsyncClient, auth: AuthContext, redaction_scope: RedactionScope
) -> None:
    """[B-245] canary 反查：真实签发/交换的原文在 `control` 全库（全表全列）零命中。

    真实边界：真实 `ChatTokenService` 签发 + 真实 API-04 交换 + 真实 `AuditService` 写入
    （含凭据的最坏载荷）+ `control` 31 张真实表的 `to_jsonb(row)::text` 反查。
    关键断言：Canonical 权威行/审计/快照/幂等回执表均无明文；审计丢键且非敏感字段存活。
    """
    scope = redaction_scope
    user_token = await _issue_via_service(scope)
    exchanged = await client.post(
        EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": SAME_ORIGIN}
    )
    assert exchanged.status_code == 200, exchanged.text
    session_token = client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token
    cookie_canary = _canary("audit-cookie")

    # 审计写入边界的最坏调用方：把凭据塞进 before/after（真实服务真实事务）。
    async with get_session_factory()() as session:
        await AuditService(session).record_config_change(
            tenant_id=scope.tenant_id,
            actor=AuditActor(account_id=auth.admin_id, source_ip="203.0.113.7"),
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=uuid.uuid4(),
            action="UPDATE",
            before={CHAT_SESSION_COOKIE: session_token, "revision": 1},
            after={"token": user_token, CHAT_CSRF_COOKIE: cookie_canary, "revision": 2},
        )
        await session.commit()

    audits = await _audit_rows(scope.tenant_id)
    assert audits, "审计行必须真实落库，丢键断言才有对象"
    audit_text = _audit_dump(audits)
    assert audits[-1].before_json == {"revision": 1}, audits[-1].before_json
    assert audits[-1].after_json == {"revision": 2}, audits[-1].after_json

    for canary in (user_token, session_token, cookie_canary):
        hits = await _canary_hits(canary)
        assert hits == {}, f"control 全库出现凭据明文：{hits}"
    assert hash_chat_token(user_token) not in audit_text, "审计连摘要都不写"
    assert len(await _token_rows(scope.tenant_id)) == 1, "权威行真实存在（反查非空扫）"
