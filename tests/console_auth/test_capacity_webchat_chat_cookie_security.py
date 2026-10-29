"""[B-241] CHAT 独立 Cookie、CSRF 与 Origin 校验（design §3.4 API-04；TASK-041）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务/HTTP 依赖**：跨上下文用例直连 `muad_console_platform.main.app`
  的真实登录会话、真实角色裁决与真实 Cookie 名（`muad_session`/`muad_csrf`）；CHAT 侧把本任务
  交付的安全依赖（`require_chat_origin`/`require_chat_csrf`/`set_chat_cookies`/`clear_chat_cookies`/
  `mark_no_store`）挂到真实 ASGI 请求/响应循环与 api-kit 真实错误封套（`install_api_foundation`）
  上——探针应用只是这些**真实依赖**的挂载点，API-04 的生产路由本体归 TASK-042（B-242），
  本文件不替它断言业务分支；
- **真实 PostgreSQL 与事务**：`control.chat_access_token` / `control.chat_session` 用真实行回读；
  "Cookie `Max-Age` 与库内剩余期限同源"直接比对行内 `issued_at`/`expires_at`（PG 服务端时间），
  并用"设置 24 小时但来源 Token 只剩 1 小时"构造"跟着行走、不跟着设置走"的反例；
- **明文卫生**：用户 Token 与会话原文只出现在各自的真实传输里，不进响应体/响应头/日志/库内；
- 本路径不发起任何外部模型调用，故"外部模型真实 HTTP 探针"边界在此不适用。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含 CHAT 新表；本文件自带按 `tenant_id`
的清理（会话→Token→用户→设置→审计，顺序满足外键），tenant 每用例唯一。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.cookies import Morsel, SimpleCookie
from pathlib import Path
from typing import Annotated, Any

import pytest
import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpResponse
from muad_api import ApiResponse, AppError, ErrorCode, install_api_foundation, ok
from muad_console_platform.api.chat_security import (
    CHAT_CSRF_COOKIE,
    CHAT_SESSION_COOKIE,
    chat_cookie_max_age,
    clear_chat_cookies,
    mark_no_store,
    require_chat_csrf,
    require_chat_origin,
    set_chat_cookies,
)
from muad_console_platform.api.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    new_csrf_token,
)
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_auth_service import ChatSessionService
from muad_console_platform.application.chat_token_service import (
    ChatTokenService,
    hash_chat_token,
)
from muad_console_platform.infrastructure.db import get_session, get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ChatAccessToken,
    ChatSession,
    ConsoleSession,
)
from muad_console_platform.infrastructure.models.control import (
    ConfigAuditLog,
    PlatformSetting,
    PlatformUser,
)
from muad_contracts.webchat import WebchatTokenExchange
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_auth.conftest import ADMIN_PASSWORD, AuthContext, login

pytestmark = pytest.mark.integration

MESSAGES_FILE = Path(__file__).resolve().parents[2] / "config/api-messages.yaml"
#: 探针应用与真实 Console 应用共用同一 Host（httpx `base_url=http://test`）。
SAME_ORIGIN = "http://test"
FOREIGN_ORIGIN = "https://evil.example"
EXCHANGE_PATH = "/api/v1/webchat/auth/exchange"
ME_PATH = "/api/v1/webchat/auth/me"
LOGOUT_PATH = "/api/v1/webchat/auth/logout"
#: 无设置版本时 `webchat.session_ttl_hours` 的 schema 默认 12 小时。
DEFAULT_SESSION_TTL_SECONDS = 12 * 3600
#: 256 位熵的 url-safe base64 至少 43 个字符（43 × 6 = 258 位 ≥ 256 位）。
MIN_TOKEN_CHARS = 43
#: API-04 的公开会话信息恰好这七个字段：多一个都是泄漏面。
CHAT_INFO_FIELDS = {
    "tenant_id",
    "user_id",
    "display_name",
    "session_id",
    "expires_at",
    "auth_context_id",
    "auth_method",
}
PURGE_STATEMENTS = (
    "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
    "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class ChatScope:
    """本用例租户里的平台用户：CHAT 身份只由 PG 的 `platform_user` 行导出。"""

    tenant_id: str
    user_id: uuid.UUID
    display_name: str


# --- 探针应用：把真实安全依赖挂到真实 ASGI 循环上（API-04 生产路由归 TASK-042） ---------


async def _probe_exchange(
    payload: WebchatTokenExchange,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse[Any]:
    """API-04 exchange 的安全层装配：Origin 门控 → 真实交换 → 同源 Max-Age → CHAT Cookie。"""
    grant = await ChatSessionService(session).exchange(payload.token)
    max_age = await chat_cookie_max_age(session, grant.info.expires_at)
    set_chat_cookies(response, grant.session_token, new_csrf_token(), max_age)
    mark_no_store(response)
    return ok(request.app.state.message_catalog, grant.info.model_dump(mode="json"))


async def _probe_me(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse[Any]:
    """API-04 me 的安全层装配：只认 CHAT Cookie，安全方法免 CSRF。"""
    token = request.cookies.get(CHAT_SESSION_COOKIE, "")
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED)
    info = await ChatSessionService(session).resolve(SecretStr(token))
    mark_no_store(response)
    return ok(request.app.state.message_catalog, info.model_dump(mode="json"))


async def _probe_logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse[Any]:
    """API-04 logout 的安全层装配：写请求先过独立 CHAT CSRF，再撤销本会话并清 CHAT Cookie。"""
    token = request.cookies.get(CHAT_SESSION_COOKIE, "")
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED)
    await ChatSessionService(session).logout(SecretStr(token))
    clear_chat_cookies(response)
    mark_no_store(response)
    return ok(request.app.state.message_catalog, {"logged_out": True})


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    install_api_foundation(app, messages_file=MESSAGES_FILE)
    router = APIRouter(prefix="/api/v1/webchat/auth")
    router.add_api_route(
        "/exchange", _probe_exchange, methods=["POST"], dependencies=[Depends(require_chat_origin)]
    )
    router.add_api_route(
        "/logout", _probe_logout, methods=["POST"], dependencies=[Depends(require_chat_csrf)]
    )
    router.add_api_route("/me", _probe_me, methods=["GET"])
    app.include_router(router)
    return app


@pytest.fixture
async def chat_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_build_probe_app())
    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as client:
        yield client


@pytest.fixture
async def chat_scope(auth: AuthContext) -> AsyncIterator[ChatScope]:
    """真实平台用户：CHAT 交换只能从 Token 记录导出用户与租户，不接受浏览器自报身份。"""
    factory = get_session_factory()
    async with factory() as session:
        user = PlatformUser(
            tenant_id=auth.tenant_id,
            user_code=f"u-{uuid.uuid4().hex[:8]}",
            display_name="Webchat User",
        )
        session.add(user)
        await session.commit()
        scope = ChatScope(
            tenant_id=auth.tenant_id, user_id=user.id, display_name="Webchat User"
        )
    try:
        yield scope
    finally:
        async with factory() as session:
            for statement in PURGE_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": auth.tenant_id})
            await session.commit()


# --- 真实服务/库内回读 -----------------------------------------------------------------


async def _issue_user_token(scope: ChatScope) -> str:
    """真实 `ChatTokenService` 首签：拿到可交换的用户 Token 原文。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            expected_revision=0,
            actor=AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.7"),
        )
        await session.commit()
        return issued.token.get_secret_value()


async def _session_rows(tenant_id: str) -> list[ChatSession]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id)
            .order_by(ChatSession.issued_at, ChatSession.id)
        )
        return list(rows)


async def _token_rows(tenant_id: str) -> list[ChatAccessToken]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .order_by(ChatAccessToken.revision)
        )
        return list(rows)


async def _audit_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ConfigAuditLog)
                .where(ConfigAuditLog.tenant_id == tenant_id)
            )
            or 0
        )


async def _revoked_at(tenant_id: str, session_id: str) -> datetime | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            select(ChatSession.revoked_at).where(
                ChatSession.tenant_id == tenant_id, ChatSession.id == uuid.UUID(session_id)
            )
        )


async def _console_session_count(digest: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ConsoleSession)
                .where(ConsoleSession.token_hash == digest)
            )
            or 0
        )


async def _chat_session_count(digest: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ChatSession)
                .where(ChatSession.token_hash == digest)
            )
            or 0
        )


async def _add_settings_revision(tenant_id: str, revision: int, document: dict[str, Any]) -> None:
    async with get_session_factory()() as session:
        session.add(
            PlatformSetting(
                tenant_id=tenant_id,
                revision=revision,
                settings_json=document,
                actor_user_id=uuid.uuid4(),
            )
        )
        await session.commit()


async def _set_token_times(
    tenant_id: str, revision: int, *, issued_at: Any, expires_at: Any
) -> None:
    """真实 SQL：把来源 Token 压成"只剩 X 分钟"的边界（不做时钟替身）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ChatAccessToken)
            .where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.revision == revision,
            )
            .values(issued_at=issued_at, expires_at=expires_at)
        )
        await session.commit()


# --- 断言辅助 -------------------------------------------------------------------------


def _cookies(response: HttpResponse) -> dict[str, Morsel[str]]:
    """从真实 `Set-Cookie` 头解析 Cookie 与属性（HttpOnly/Secure/SameSite/Max-Age/Path）。"""
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return dict(jar.items())


def _assert_chat_flags(morsel: Morsel[str], *, httponly: bool, secure: bool, max_age: int) -> None:
    assert bool(morsel["httponly"]) is httponly, "会话 Cookie 必须 HttpOnly、CSRF Cookie 必须可读"
    assert bool(morsel["secure"]) is secure, "Secure 只随共享 Cookie 策略（仅非 dev 打开）"
    assert morsel["samesite"] == "strict"
    assert morsel["path"] == "/"
    assert morsel["max-age"] == str(max_age), "Max-Age 必须与库内剩余期限同源"


def _row_dump(row: Any) -> str:
    return str({column.name: getattr(row, column.name) for column in row.__table__.columns})


def _log_dump(records: list[logging.LogRecord]) -> str:
    return str([{key: str(value) for key, value in record.__dict__.items()} for record in records])


def _non_cookie_headers(response: HttpResponse) -> str:
    """除 `Set-Cookie` 外的响应头：会话原文只允许出现在下发给浏览器的 Cookie 里。"""
    return str(
        [(name, value) for name, value in response.headers.items() if name.lower() != "set-cookie"]
    )


def _expires_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- B-241：CHAT 独立 Cookie 与 Max-Age 同源 ------------------------------------------


async def test_b241_chat_cookies_are_independent_and_max_age_tracks_the_stored_session(
    chat_client: AsyncClient, chat_scope: ChatScope, caplog: pytest.LogCaptureFixture
) -> None:
    """[B-241] 交换写 CHAT 独立 Cookie（HttpOnly/Secure/SameSite/Max-Age 同源），凭据零外泄。"""
    caplog.set_level(logging.INFO)
    user_token = await _issue_user_token(chat_scope)
    audits_before = await _audit_count(chat_scope.tenant_id)
    assert audits_before == 1, "只有签发写审计"

    response = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store", "含凭据的交换响应不得被中间层缓存"
    body = response.json()
    assert body["code"] == "0" and set(body["data"]) == CHAT_INFO_FIELDS
    info = body["data"]
    assert info["tenant_id"] == chat_scope.tenant_id
    assert info["user_id"] == str(chat_scope.user_id)
    assert info["display_name"] == chat_scope.display_name
    assert info["auth_method"] == "USER_TOKEN" and info["auth_context_id"] == info["session_id"]

    cookies = _cookies(response)
    assert set(cookies) == {CHAT_SESSION_COOKIE, CHAT_CSRF_COOKIE}, "交换只写 CHAT 两枚 Cookie"
    assert {CHAT_SESSION_COOKIE, CHAT_CSRF_COOKIE}.isdisjoint({SESSION_COOKIE, CSRF_COOKIE}), (
        "CHAT Cookie 名必须与管理会话 Cookie 完全分离"
    )
    row = (await _session_rows(chat_scope.tenant_id))[0]
    remaining = int((row.expires_at - row.issued_at).total_seconds())
    assert abs(remaining - DEFAULT_SESSION_TTL_SECONDS) <= 1, (
        "Cookie Max-Age = min(交换 + session_ttl, 来源 Token 到期)；默认 12 小时"
    )
    _assert_chat_flags(cookies[CHAT_SESSION_COOKIE], httponly=True, secure=False, max_age=remaining)
    _assert_chat_flags(cookies[CHAT_CSRF_COOKIE], httponly=False, secure=False, max_age=remaining)
    assert cookies[CHAT_SESSION_COOKIE]["max-age"] == cookies[CHAT_CSRF_COOKIE]["max-age"]
    assert _expires_at(info["expires_at"]) == row.expires_at
    assert info["session_id"] == str(row.id)

    session_token = chat_client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token and session_token != user_token and len(session_token) >= MIN_TOKEN_CHARS
    assert row.token_hash == hash_chat_token(session_token)
    assert row.token_hash != hash_chat_token(user_token), "会话摘要与用户 Token 摘要相互独立"
    assert session_token not in _row_dump(row) and user_token not in _row_dump(row)

    for secret in (user_token, session_token):
        assert secret not in response.text
        assert secret not in _non_cookie_headers(response), "凭据只准出现在下发的 HttpOnly Cookie 里"
        assert secret not in _log_dump(caplog.records)
    assert hash_chat_token(session_token) not in response.text
    assert await _audit_count(chat_scope.tenant_id) == audits_before, "交换不是配置变更，不写审计"

    # 交换不改写来源 Token：不续期、不撤销、不换身份。
    tokens = await _token_rows(chat_scope.tenant_id)
    assert [(item.revision, item.revoked_at) for item in tokens] == [(1, None)]


async def test_b241_max_age_is_sourced_from_the_session_row_not_the_settings_ttl(
    chat_client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-241] `Max-Age` 跟着**行**走：设置 24 小时但来源只剩 1 小时时必须 ~3600，不是 86400。"""
    await _add_settings_revision(chat_scope.tenant_id, 1, {"webchat": {"session_ttl_hours": 24}})
    user_token = await _issue_user_token(chat_scope)
    await _set_token_times(
        chat_scope.tenant_id,
        1,
        issued_at=sa.text("now() - interval '1 hour'"),
        expires_at=sa.text("now() + interval '1 hour'"),
    )

    response = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})

    assert response.status_code == 200
    row = (await _session_rows(chat_scope.tenant_id))[0]
    token_row = (await _token_rows(chat_scope.tenant_id))[0]
    assert row.expires_at == token_row.expires_at, "会话期限被来源 Token 压到同一到期时刻"
    remaining = int((row.expires_at - row.issued_at).total_seconds())
    cookies = _cookies(response)
    _assert_chat_flags(cookies[CHAT_SESSION_COOKIE], httponly=True, secure=False, max_age=remaining)
    _assert_chat_flags(cookies[CHAT_CSRF_COOKIE], httponly=False, secure=False, max_age=remaining)
    assert timedelta(minutes=59) < row.expires_at - row.issued_at <= timedelta(hours=1)
    assert remaining < 24 * 3600, "Max-Age 不得取设置 TTL（86400）"

    # 剩余期限 ≤ 0：显式 `CHAT_SESSION_EXPIRED`，不写"已过期"Cookie 掩盖事实；正剩余向下取整。
    async with get_session_factory()() as session:
        now = await session.scalar(select(sa.func.now()))
        assert isinstance(now, datetime)
        with pytest.raises(AppError) as expired:
            await chat_cookie_max_age(session, now)
        assert expired.value.code == ErrorCode.CHAT_SESSION_EXPIRED
        with pytest.raises(AppError) as expired_negative:
            await chat_cookie_max_age(session, now - timedelta(seconds=1))
        assert expired_negative.value.code == ErrorCode.CHAT_SESSION_EXPIRED
        assert await chat_cookie_max_age(session, now + timedelta(seconds=1)) == 1
        assert await chat_cookie_max_age(session, now + timedelta(seconds=1, microseconds=900_000)) == 1
        await session.rollback()


async def test_b241_cookie_secure_flag_follows_the_shared_non_dev_policy(
    chat_client: AsyncClient, chat_scope: ChatScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-241] `Secure` 沿用共享 Cookie 策略：非 dev 打开，dev 关闭（同一来源 `SharedSettings.env`）。"""
    monkeypatch.setenv("ENV", "prod")
    user_token = await _issue_user_token(chat_scope)

    response = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})

    assert response.status_code == 200
    cookies = _cookies(response)
    row = (await _session_rows(chat_scope.tenant_id))[0]
    remaining = int((row.expires_at - row.issued_at).total_seconds())
    _assert_chat_flags(cookies[CHAT_SESSION_COOKIE], httponly=True, secure=True, max_age=remaining)
    _assert_chat_flags(cookies[CHAT_CSRF_COOKIE], httponly=False, secure=True, max_age=remaining)


# --- B-241：Origin 与独立 CSRF ---------------------------------------------------------


async def test_b241_exchange_requires_a_same_origin_request_before_credentials(
    chat_client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-241] 交换 POST 验 Origin：异源/`null` 403 且零落行；同源放行；不接受可信 user_id/tenant。"""
    user_token = await _issue_user_token(chat_scope)
    audits_before = await _audit_count(chat_scope.tenant_id)

    foreign = await chat_client.post(
        EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": FOREIGN_ORIGIN}
    )
    assert foreign.status_code == 403 and foreign.json()["code"] == "FORBIDDEN"
    assert user_token not in foreign.text
    assert foreign.headers.get_list("set-cookie") == [], "被拒交换不得下发任何 Cookie"
    assert await _session_rows(chat_scope.tenant_id) == []
    assert await _audit_count(chat_scope.tenant_id) == audits_before

    opaque = await chat_client.post(
        EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": "null"}
    )
    assert opaque.status_code == 403 and opaque.json()["code"] == "FORBIDDEN"
    assert await _session_rows(chat_scope.tenant_id) == []

    # 判定顺序：Origin 先于请求体/凭据判定，异源请求体问题不得泄漏成 422。
    cross_origin_body = await chat_client.post(
        EXCHANGE_PATH,
        json={"token": user_token, "user_id": str(uuid.uuid4()), "tenant_id": "attacker-tenant"},
        headers={"Origin": FOREIGN_ORIGIN},
    )
    assert cross_origin_body.status_code == 403
    assert await _session_rows(chat_scope.tenant_id) == []

    # 同源 + 自报 user_id/tenant：契约层 422（公开路由只接受 token），仍零落行。
    same_origin_body = await chat_client.post(
        EXCHANGE_PATH,
        json={"token": user_token, "user_id": str(uuid.uuid4()), "tenant_id": "attacker-tenant"},
        headers={"Origin": SAME_ORIGIN},
    )
    assert same_origin_body.status_code == 422
    assert same_origin_body.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _session_rows(chat_scope.tenant_id) == []

    accepted = await chat_client.post(
        EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": SAME_ORIGIN}
    )
    assert accepted.status_code == 200
    assert len(await _session_rows(chat_scope.tenant_id)) == 1
    # 非浏览器客户端（无 ambient 凭据面）缺 Origin 仍可用：CSRF 面由 Cookie/CSRF 承担。
    browserless = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})
    assert browserless.status_code == 200
    assert len(await _session_rows(chat_scope.tenant_id)) == 2


async def test_b241_chat_writes_require_the_independent_chat_csrf_pair(
    chat_client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-241] 写请求核独立 CHAT CSRF：Console 的 `muad_csrf` 对不能授权 CHAT，失败零副作用。"""
    user_token = await _issue_user_token(chat_scope)
    exchanged = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})
    assert exchanged.status_code == 200
    session_token = chat_client.cookies.get(CHAT_SESSION_COOKIE)
    chat_csrf = chat_client.cookies.get(CHAT_CSRF_COOKIE)
    assert session_token and chat_csrf and chat_client.cookies.get(CSRF_COOKIE) is None
    row = (await _session_rows(chat_scope.tenant_id))[0]

    # 安全方法免 CSRF：GET /me 只凭 HttpOnly CHAT Cookie 恢复会话，且不滑动续期（零 Set-Cookie）。
    me = await chat_client.get(ME_PATH)
    assert me.status_code == 200 and me.headers["cache-control"] == "no-store"
    assert me.json()["data"]["session_id"] == str(row.id)
    assert me.headers.get_list("set-cookie") == [], "普通请求不重发/不延长 Cookie"

    # 缺 CHAT CSRF：403 且零副作用（会话仍活跃、不下发清除 Cookie）。
    missing = await chat_client.post(LOGOUT_PATH)
    assert missing.status_code == 403 and missing.json()["code"] == "FORBIDDEN"
    assert await _revoked_at(chat_scope.tenant_id, str(row.id)) is None
    assert missing.headers.get_list("set-cookie") == []

    # Console 的 CSRF 对（`muad_csrf` + `X-CSRF-Token`）在 CHAT 写请求上无效。
    chat_client.cookies.delete(CHAT_CSRF_COOKIE)
    chat_client.cookies.set(CSRF_COOKIE, "console-csrf-canary", path="/")
    console_pair = await chat_client.post(LOGOUT_PATH, headers={CSRF_HEADER: "console-csrf-canary"})
    assert console_pair.status_code == 403 and console_pair.json()["code"] == "FORBIDDEN"
    assert await _revoked_at(chat_scope.tenant_id, str(row.id)) is None

    # CHAT CSRF Cookie 在、头缺失/错值：403。
    chat_client.cookies.set(CHAT_CSRF_COOKIE, chat_csrf, path="/")
    wrong = await chat_client.post(LOGOUT_PATH, headers={CSRF_HEADER: "not-the-chat-csrf"})
    assert wrong.status_code == 403 and wrong.json()["code"] == "FORBIDDEN"
    assert await _revoked_at(chat_scope.tenant_id, str(row.id)) is None

    # 正确 CHAT 对：受理并真实撤销本会话；只清 CHAT Cookie，不动 Console Cookie。
    accepted = await chat_client.post(LOGOUT_PATH, headers={CSRF_HEADER: chat_csrf})
    assert accepted.status_code == 200 and accepted.json()["data"] == {"logged_out": True}
    assert accepted.headers["cache-control"] == "no-store"
    revoked_at = await _revoked_at(chat_scope.tenant_id, str(row.id))
    assert revoked_at is not None
    cleared = _cookies(accepted)
    assert set(cleared) == {CHAT_SESSION_COOKIE, CHAT_CSRF_COOKIE}
    assert all(morsel["max-age"] == "0" for morsel in cleared.values())
    assert set(cleared).isdisjoint({SESSION_COOKIE, CSRF_COOKIE}), "登出不得触碰管理会话 Cookie"

    # 撤销后 CHAT Cookie 立即失效；重放登出幂等（不改时间戳）。
    assert (await chat_client.get(ME_PATH)).status_code == 401
    chat_client.cookies.set(CHAT_SESSION_COOKIE, session_token, path="/")
    chat_client.cookies.set(CHAT_CSRF_COOKIE, chat_csrf, path="/")
    replayed = await chat_client.post(LOGOUT_PATH, headers={CSRF_HEADER: chat_csrf})
    assert replayed.status_code == 200
    assert await _revoked_at(chat_scope.tenant_id, str(row.id)) == revoked_at


# --- B-241：CHAT 与 Console 凭据互不通用 ------------------------------------------------


async def test_b241_chat_and_console_credentials_never_cross_authenticate(
    client: AsyncClient, chat_client: AsyncClient, auth: AuthContext, chat_scope: ChatScope
) -> None:
    """[B-241] CHAT 凭据不可当 Console 凭据（反向亦然）：Console 身份不是 PlatformUser。"""
    # 真实 Console 登录（真实账号/会话裁决）与真实 CHAT 交换（真实 PlatformUser Token）。
    logged_in = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert logged_in.status_code == 200
    console_token = client.cookies.get(SESSION_COOKIE)
    assert console_token
    user_token = await _issue_user_token(chat_scope)
    exchanged = await chat_client.post(EXCHANGE_PATH, json={"token": user_token})
    assert exchanged.status_code == 200
    chat_token = chat_client.cookies.get(CHAT_SESSION_COOKIE)
    assert chat_token and chat_token != console_token

    # ① CHAT 会话原文冒充 Console：Bearer / `muad_session` Cookie 一律 401，且不落成 Console 会话。
    digest = hash_chat_token(chat_token)
    bearer = {"Authorization": f"Bearer {chat_token}"}
    bearer_me = await client.get("/api/v1/auth/me", headers=bearer)
    bearer_users = await client.get("/api/v1/users", headers=bearer)
    client.cookies.set(SESSION_COOKIE, chat_token, path="/")
    cookie_me = await client.get("/api/v1/auth/me")
    for response in (bearer_me, bearer_users, cookie_me):
        assert response.status_code == 401 and response.json()["code"] == "UNAUTHORIZED"
        assert chat_token not in response.text and digest not in response.text
    assert await _console_session_count(digest) == 0, "CHAT 摘要不得落成 Console 会话"
    client.cookies.delete(SESSION_COOKIE)

    # ② Console 的 Cookie 名不进 CHAT 认证面：只有 `muad_session` 时 /me 401。
    chat_client.cookies.clear()
    chat_client.cookies.set(SESSION_COOKIE, console_token, path="/")
    console_cookie_me = await chat_client.get(ME_PATH)
    assert console_cookie_me.status_code == 401
    assert console_cookie_me.json()["code"] == "UNAUTHORIZED"
    assert console_token not in console_cookie_me.text

    # ③ Console 会话原文即使被塞进 CHAT Cookie：真实服务按摘要定位不到会话，401 且零落行。
    chat_client.cookies.set(CHAT_SESSION_COOKIE, console_token, path="/")
    cross_me = await chat_client.get(ME_PATH)
    assert cross_me.status_code == 401 and cross_me.json()["code"] == "UNAUTHORIZED"
    assert console_token not in cross_me.text
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as anonymous:
            await ChatSessionService(session).resolve(SecretStr(console_token))
    assert anonymous.value.code == ErrorCode.UNAUTHORIZED
    assert await _chat_session_count(hash_chat_token(console_token)) == 0

    # ④ Console 身份不是 PlatformUser：Console 会话原文不能换取 CHAT 会话；Console 账号 id 不是平台用户。
    async with get_session_factory()() as session:
        with pytest.raises(AppError) as invalid:
            await ChatSessionService(session).exchange(SecretStr(console_token))
    assert invalid.value.code == ErrorCode.CHAT_TOKEN_INVALID
    async with get_session_factory()() as session:
        assert await session.scalar(select(PlatformUser.id).where(PlatformUser.id == auth.admin_id)) is None
    assert await _chat_session_count(hash_chat_token(console_token)) == 0

    # ⑤ 交换与登录各自只下发自己的 Cookie（互不越界）。
    assert set(_cookies(exchanged)).isdisjoint({SESSION_COOKIE, CSRF_COOKIE})
    assert set(_cookies(logged_in)) == {SESSION_COOKIE, CSRF_COOKIE}
    rows = await _session_rows(chat_scope.tenant_id)
    assert len(rows) == 1 and rows[0].token_hash == digest
