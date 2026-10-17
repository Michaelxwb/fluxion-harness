"""[B-242] CHAT exchange/me/logout 公开认证路由（design §3.4 API-04；TASK-042）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务/HTTP 依赖**：直连 `muad_console_platform.main.app`（`tests/console_auth/`
  `conftest.py` 的 `client` fixture）——真实路由表、真实 api-kit 封套/错误目录与真实 ASGI 请求循环；
  全程不建立任何 Console 会话，验证三条路由确实在**管理登录守卫之外**；
- **真实 PostgreSQL 与事务**：`control.platform_user` / `chat_access_token` / `chat_session` 用真实行
  回读（交换落行、登出只撤本会话、来源 Token 零改写）；到期边界用真实 SQL 压时间，不做时钟替身；
- **真实 Cookie 头**：`Set-Cookie` 头解析（HttpOnly/Max-Age=0 清除）与 CookieJar 回放；
- 明文卫生：用户 Token/会话原文只进各自传输面，不进响应体/非 Cookie 响应头/日志/库内；
- 本路径不发起任何外部模型调用，故"外部模型真实 HTTP 探针"边界在此不适用。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含 CHAT 新表；本文件自带按 `tenant_id`
的清理（会话→Token→用户→设置→审计，顺序满足外键），tenant 每用例唯一。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from http.cookies import Morsel, SimpleCookie
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from httpx import Response as HttpResponse
from muad_console_platform.api.chat_security import CHAT_CSRF_COOKIE, CHAT_SESSION_COOKIE
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_token_service import (
    ChatTokenService,
    hash_chat_token,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ChatAccessToken, ChatSession
from muad_console_platform.infrastructure.models.control import ConfigAuditLog, PlatformUser
from sqlalchemy import select, text

from console_auth.conftest import AuthContext

pytestmark = pytest.mark.integration

#: 与真实 Console 应用共用同一 Host（httpx `base_url=http://test`）。
SAME_ORIGIN = "http://test"
FOREIGN_ORIGIN = "https://evil.example"
EXCHANGE_PATH = "/api/v1/webchat/auth/exchange"
ME_PATH = "/api/v1/webchat/auth/me"
LOGOUT_PATH = "/api/v1/webchat/auth/logout"
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
#: 管理角色的 Console 语义字段：CHAT principal 不得继承任何一个。
CONSOLE_ROLE_FIELDS = {"role", "permissions", "username", "account_id"}
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
        scope = ChatScope(tenant_id=auth.tenant_id, user_id=user.id, display_name="Webchat User")
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


async def _add_platform_user(tenant_id: str, display_name: str) -> PlatformUser:
    async with get_session_factory()() as session:
        user = PlatformUser(
            tenant_id=tenant_id,
            user_code=f"u-{uuid.uuid4().hex[:8]}",
            display_name=display_name,
        )
        session.add(user)
        await session.commit()
        return user


async def _issue_token_for(tenant_id: str, user_id: uuid.UUID) -> str:
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=tenant_id,
            user_id=user_id,
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


async def _expire_session(session_id: uuid.UUID) -> None:
    """真实 SQL：把会话压到"刚刚过期"（PG 服务端时间），不做时钟替身。

    约束 `expires_at > issued_at` 是库级事实：两条时间一起平移，只改到期点是非法行。
    """
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(
                issued_at=sa.text("now() - interval '2 hours'"),
                expires_at=sa.text("now() - interval '1 second'"),
            )
        )
        await session.commit()


# --- 断言辅助 -------------------------------------------------------------------------


def _cookies(response: HttpResponse) -> dict[str, Morsel[str]]:
    """从真实 `Set-Cookie` 头解析 Cookie 与属性（HttpOnly/Max-Age/Path）。"""
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return dict(jar.items())


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


# --- B-242：公开路由在管理登录守卫之外 ---------------------------------------------------


async def test_b242_public_routes_run_outside_the_console_login_guard(
    client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-242] 无 Console 会话即可交换/恢复/登出；CHAT 凭据不授予 Console 权限。"""
    assert client.cookies.get(SESSION_COOKIE) is None, "用例起点没有任何管理会话"
    user_token = await _issue_user_token(chat_scope)

    # 管理登录守卫之外：无 `muad_session` 也完成交换，且不下发 Console 会话 Cookie。
    exchanged = await client.post(EXCHANGE_PATH, json={"token": user_token})
    assert exchanged.status_code == 200
    assert exchanged.headers["cache-control"] == "no-store"
    assert client.cookies.get(SESSION_COOKIE) is None, "CHAT 交换不得建立管理会话"

    # 无管理角色继承：CHAT 凭据打管理端点仍 401（CHAT 路由不引入 Console 门控旁路）。
    for path in ("/api/v1/auth/me", "/api/v1/users"):
        denied = await client.get(path)
        assert denied.status_code == 401, path
        assert denied.json()["code"] == "UNAUTHORIZED", path

    # 无 Console 会话即可恢复身份与登出。
    me = await client.get(ME_PATH)
    assert me.status_code == 200 and me.headers["cache-control"] == "no-store"
    csrf = client.cookies.get(CHAT_CSRF_COOKIE)
    assert csrf
    logout = await client.post(LOGOUT_PATH, headers={CSRF_HEADER: csrf})
    assert logout.status_code == 200
    assert logout.headers["cache-control"] == "no-store"
    assert logout.json()["data"] == {"logged_out": True}


# --- B-242：exchange 只接受 Token -------------------------------------------------------


async def test_b242_exchange_only_accepts_the_opaque_token_field(
    client: AsyncClient, chat_scope: ChatScope, caplog: pytest.LogCaptureFixture
) -> None:
    """[B-242] 交换只接受 `token`：自报身份 422、Origin 先于凭据/请求体、身份从记录导出。"""
    caplog.set_level(logging.INFO)
    user_token = await _issue_user_token(chat_scope)
    audits_before = await _audit_count(chat_scope.tenant_id)
    assert audits_before == 1, "只有签发写审计"

    # 自报 user_id/tenant：契约层 422，零落行、零 Cookie、不回显 Token。
    forged = await client.post(
        EXCHANGE_PATH,
        json={"token": user_token, "user_id": str(uuid.uuid4()), "tenant_id": "attacker-tenant"},
        headers={"Origin": SAME_ORIGIN},
    )
    assert forged.status_code == 422
    assert forged.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert user_token not in forged.text
    assert forged.headers.get_list("set-cookie") == []
    assert await _session_rows(chat_scope.tenant_id) == []

    # 没有 JWT/provider 认证方：只有不透明 `token` 字符串字段能进入交换。
    for payload in ({"jwt": "whatever"}, {"token": 123}, {"token": "   "}, {}):
        rejected = await client.post(EXCHANGE_PATH, json=payload)
        assert rejected.status_code == 422, payload
        assert rejected.json()["code"] == "COMMON_VALIDATION_ERROR", payload
    assert await _session_rows(chat_scope.tenant_id) == []

    # Origin 先于凭据与请求体：异源（含越界请求体）与 `null` 一律 403，零副作用。
    for body in ({"token": user_token}, {"token": user_token, "tenant_id": "attacker-tenant"}):
        foreign = await client.post(EXCHANGE_PATH, json=body, headers={"Origin": FOREIGN_ORIGIN})
        assert foreign.status_code == 403 and foreign.json()["code"] == "FORBIDDEN"
        assert foreign.headers.get_list("set-cookie") == []
        assert user_token not in foreign.text
    opaque = await client.post(EXCHANGE_PATH, json={"token": user_token}, headers={"Origin": "null"})
    assert opaque.status_code == 403 and opaque.json()["code"] == "FORBIDDEN"
    assert await _session_rows(chat_scope.tenant_id) == []

    # Bearer 不是交换通道：无请求体也不接受（没有 JWT 交换路径）。
    bearer = await client.post(EXCHANGE_PATH, headers={"Authorization": f"Bearer {user_token}"})
    assert bearer.status_code == 422
    assert await _session_rows(chat_scope.tenant_id) == []

    # 同源合法交换：身份从 Token 记录导出，`X-Tenant-Id` 头不改写主体。
    accepted = await client.post(
        EXCHANGE_PATH,
        json={"token": user_token},
        headers={"Origin": SAME_ORIGIN, "X-Tenant-Id": "attacker-tenant"},
    )
    assert accepted.status_code == 200
    data = accepted.json()["data"]
    assert set(data) == CHAT_INFO_FIELDS
    assert data["tenant_id"] == chat_scope.tenant_id, "租户来自 Token 记录，不接受请求头声明"
    assert data["user_id"] == str(chat_scope.user_id)
    assert data["display_name"] == chat_scope.display_name

    # 明文卫生：两枚原文只进 HttpOnly Cookie，不进响应体/非 Cookie 头/日志/库内。
    session_token = client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token
    row = (await _session_rows(chat_scope.tenant_id))[0]
    assert row.token_hash == hash_chat_token(session_token)
    for secret in (user_token, session_token):
        assert secret not in accepted.text
        assert secret not in _non_cookie_headers(accepted)
        assert secret not in _log_dump(caplog.records)
        assert secret not in _row_dump(row)
    assert await _audit_count(chat_scope.tenant_id) == audits_before, "交换不是配置变更，不写审计"
    tokens = await _token_rows(chat_scope.tenant_id)
    assert [(item.revision, item.revoked_at) for item in tokens] == [(1, None)]


# --- B-242：me 返回统一 principal --------------------------------------------------------


async def test_b242_me_returns_the_unified_principal_without_sliding(
    client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-242] me 返回七字段统一 principal（期限/auth_context 与库行同源），不滑动、不继承角色。"""
    user_token = await _issue_user_token(chat_scope)
    exchanged = await client.post(EXCHANGE_PATH, json={"token": user_token})
    assert exchanged.status_code == 200
    session_token = client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token
    row = (await _session_rows(chat_scope.tenant_id))[0]

    me = await client.get(ME_PATH)
    assert me.status_code == 200 and me.headers["cache-control"] == "no-store"
    data = me.json()["data"]
    assert set(data) == CHAT_INFO_FIELDS
    assert data["tenant_id"] == chat_scope.tenant_id
    assert data["user_id"] == str(chat_scope.user_id)
    assert data["display_name"] == chat_scope.display_name
    assert data["session_id"] == str(row.id)
    assert data["auth_context_id"] == data["session_id"], "auth_context 就是本 CHAT 会话"
    assert data["auth_method"] == "USER_TOKEN"
    assert _expires_at(data["expires_at"]) == row.expires_at, "期限与权威会话行同源"
    assert CONSOLE_ROLE_FIELDS.isdisjoint(data), "CHAT principal 不继承管理角色/权限"
    assert me.headers.get_list("set-cookie") == [], "普通请求不重发/不延长 Cookie"

    # 身份来自服务端记录：换一个用户的 Token 就是另一个主体（不接受浏览器自报）。
    other_user = await _add_platform_user(chat_scope.tenant_id, "Second Webchat User")
    other_token = await _issue_token_for(chat_scope.tenant_id, other_user.id)
    client.cookies.clear()
    swapped = await client.post(EXCHANGE_PATH, json={"token": other_token})
    assert swapped.status_code == 200
    swapped_me = await client.get(ME_PATH)
    assert swapped_me.status_code == 200
    swapped_data = swapped_me.json()["data"]
    assert swapped_data["user_id"] == str(other_user.id)
    assert swapped_data["display_name"] == "Second Webchat User"
    assert swapped_data["tenant_id"] == chat_scope.tenant_id
    assert swapped_data["session_id"] != str(row.id)

    # 未知 CHAT 凭据：401，且不回显输入值。
    client.cookies.clear()
    client.cookies.set(CHAT_SESSION_COOKIE, "not-a-real-chat-session", path="/")
    unknown = await client.get(ME_PATH)
    assert unknown.status_code == 401 and unknown.json()["code"] == "UNAUTHORIZED"
    assert "not-a-real-chat-session" not in unknown.text

    # 无 JWT/provider 通道：Bearer 不是 CHAT 凭据（只有独立 CHAT Cookie 参与解析）。
    client.cookies.clear()
    bearer = await client.get(ME_PATH, headers={"Authorization": f"Bearer {session_token}"})
    assert bearer.status_code == 401 and bearer.json()["code"] == "UNAUTHORIZED"
    assert session_token not in bearer.text

    # 到期会话：路由显式透传 `CHAT_SESSION_EXPIRED`（不静默 200）。
    await _expire_session(row.id)
    client.cookies.set(CHAT_SESSION_COOKIE, session_token, path="/")
    expired = await client.get(ME_PATH)
    assert expired.status_code == 401 and expired.json()["code"] == "CHAT_SESSION_EXPIRED"


# --- B-242：logout 幂等且只撤本会话 ------------------------------------------------------


async def test_b242_logout_revokes_only_the_presented_session_idempotently(
    client: AsyncClient, chat_scope: ChatScope
) -> None:
    """[B-242] 登出只撤本会话（幂等、可重放），其他设备与来源 Token 不受影响。"""
    user_token = await _issue_user_token(chat_scope)
    first = await client.post(EXCHANGE_PATH, json={"token": user_token})
    assert first.status_code == 200
    session_a = client.cookies.get(CHAT_SESSION_COOKIE)
    csrf_a = client.cookies.get(CHAT_CSRF_COOKIE)
    second = await client.post(EXCHANGE_PATH, json={"token": user_token})
    assert second.status_code == 200
    session_b = client.cookies.get(CHAT_SESSION_COOKIE)
    csrf_b = client.cookies.get(CHAT_CSRF_COOKIE)
    assert session_a and csrf_a and session_b and csrf_b and session_a != session_b
    rows = await _session_rows(chat_scope.tenant_id)
    assert len(rows) == 2
    row_a = next(item for item in rows if item.token_hash == hash_chat_token(session_a))
    row_b = next(item for item in rows if item.token_hash == hash_chat_token(session_b))
    tokens_before = [(item.revision, item.revoked_at) for item in await _token_rows(chat_scope.tenant_id)]

    # 缺独立 CHAT CSRF 对：403 且零副作用（会话未撤销、零清除 Cookie）。
    client.cookies.set(CHAT_SESSION_COOKIE, session_a, path="/")
    client.cookies.delete(CHAT_CSRF_COOKIE)
    missing = await client.post(LOGOUT_PATH)
    assert missing.status_code == 403 and missing.json()["code"] == "FORBIDDEN"
    assert await _revoked_at(chat_scope.tenant_id, str(row_a.id)) is None
    assert missing.headers.get_list("set-cookie") == []

    # 正确 CHAT 对：只撤本会话、只清 CHAT 两枚 Cookie（Max-Age=0），响应 no-store。
    client.cookies.set(CHAT_CSRF_COOKIE, csrf_a, path="/")
    accepted = await client.post(LOGOUT_PATH, headers={CSRF_HEADER: csrf_a})
    assert accepted.status_code == 200
    assert accepted.json()["data"] == {"logged_out": True}
    assert accepted.headers["cache-control"] == "no-store"
    cleared = _cookies(accepted)
    assert set(cleared) == {CHAT_SESSION_COOKIE, CHAT_CSRF_COOKIE}
    assert all(morsel["max-age"] == "0" for morsel in cleared.values())
    assert set(cleared).isdisjoint({SESSION_COOKIE, CSRF_COOKIE}), "登出不得触碰管理会话 Cookie"
    revoked_at_a = await _revoked_at(chat_scope.tenant_id, str(row_a.id))
    assert revoked_at_a is not None

    # 其他设备会话不受影响：仍可 /me 恢复（不连带撤销）。
    client.cookies.set(CHAT_SESSION_COOKIE, session_b, path="/")
    client.cookies.set(CHAT_CSRF_COOKIE, csrf_b, path="/")
    other = await client.get(ME_PATH)
    assert other.status_code == 200 and other.json()["data"]["session_id"] == str(row_b.id)

    # 被撤会话读取明确失效；重放登出幂等（`revoked_at` 时间戳不变，仍 200）。
    client.cookies.set(CHAT_SESSION_COOKIE, session_a, path="/")
    client.cookies.set(CHAT_CSRF_COOKIE, csrf_a, path="/")
    stale_me = await client.get(ME_PATH)
    assert stale_me.status_code == 401 and stale_me.json()["code"] == "CHAT_SESSION_REVOKED"
    replayed = await client.post(LOGOUT_PATH, headers={CSRF_HEADER: csrf_a})
    assert replayed.status_code == 200
    assert await _revoked_at(chat_scope.tenant_id, str(row_a.id)) == revoked_at_a

    # 只有 CSRF 而无可撤销凭据：明确 401，不静默假装登出。
    client.cookies.delete(CHAT_SESSION_COOKIE)
    anonymous = await client.post(LOGOUT_PATH, headers={CSRF_HEADER: csrf_a})
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "UNAUTHORIZED"
    assert anonymous.headers.get_list("set-cookie") == []

    # 登出不撤来源 Token：行零改写，且同 Token 仍可再次交换（新会话）。
    token_state = [(item.revision, item.revoked_at) for item in await _token_rows(chat_scope.tenant_id)]
    assert token_state == tokens_before
    again = await client.post(EXCHANGE_PATH, json={"token": user_token})
    assert again.status_code == 200
    assert len(await _session_rows(chat_scope.tenant_id)) == 3
