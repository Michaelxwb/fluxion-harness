"""[B-239] 用户详情 Webchat Token 管理的 ADMIN 端点（design §3.4 API-03；TASK-039）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务/HTTP 依赖**：ASGI 直连 `muad_console_platform.main.app`，真实登录会话、
  真实 ADMIN/BUILDER 角色裁决与 CSRF 校验，不伪造主体；
- **真实 PostgreSQL 与事务**：`control.chat_access_token` / `control.chat_session` 用真实行做
  版本单胜与"关联会话同事务撤销"的证据；回执表（`control.skill_import_idempotency` /
  `control.platform_setting_idempotency`）按租户真实计数；
- **明文卫生**：原文只在首签/重置的本次响应里出现一次；GET、被拒请求、重放、审计与库内一律不可见。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含 Token 新表；本夹具自带按 `tenant_id`
的清理（会话→Token→用户→设置→回执→审计），tenant 每用例唯一。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_token_service import ChatTokenService
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ChatAccessToken, ChatSession
from muad_console_platform.infrastructure.models.control import ConfigAuditLog, PlatformUser
from sqlalchemy import select, text

from console_auth.conftest import (
    ADMIN_PASSWORD,
    BUILDER_PASSWORD,
    AuthContext,
    csrf_headers,
    login,
    tenant_headers,
)

pytestmark = pytest.mark.integration

URL_TEMPLATE = "/api/v1/users/{user_id}/webchat-token"
#: GET 只元信息：多一个字段都是泄漏面（design §3.4 API-03）。
METADATA_FIELDS = {"configured", "status", "token_id", "revision", "issued_at", "expires_at"}
ISSUED_FIELDS = METADATA_FIELDS | {"token"}
AUDIT_RESOURCE_TYPE = "CHAT_ACCESS_TOKEN"
NOT_CONFIGURED = {
    "configured": False,
    "status": "NOT_CONFIGURED",
    "token_id": None,
    "revision": 0,
    "issued_at": None,
    "expires_at": None,
}
#: 256 位熵的 url-safe base64 至少 43 个字符（43 × 6 = 258 位 ≥ 256 位）。
MIN_TOKEN_CHARS = 43
RECEIPT_TABLES = ("skill_import_idempotency", "platform_setting_idempotency")
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
class TokenScope:
    """本用例租户里的目标用户 + 另一个租户里"确实已有 Token"的用户。"""

    tenant_id: str
    user_id: uuid.UUID
    foreign_tenant_id: str
    foreign_user_id: uuid.UUID


def _url(user_id: uuid.UUID | str) -> str:
    return URL_TEMPLATE.format(user_id=user_id)


def _manage_headers(auth: AuthContext, client: AsyncClient) -> dict[str, str]:
    """ADMIN 管理请求头：租户头 + 真实登录会话换来的 CSRF token（不伪造身份）。"""
    return {**tenant_headers(auth), **csrf_headers(client)}


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for statement in PURGE_STATEMENTS:
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def token_scope(auth: AuthContext) -> AsyncIterator[TokenScope]:
    """两个租户各一个平台用户：跨租户的 Token 必须读不到、也写不了。"""
    foreign_tenant_id = f"tok-api-foreign-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        users = [
            PlatformUser(
                tenant_id=auth.tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Token Owner",
            ),
            PlatformUser(
                tenant_id=foreign_tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Foreign Token Owner",
            ),
        ]
        session.add_all(users)
        await session.commit()
        scope = TokenScope(
            tenant_id=auth.tenant_id,
            user_id=users[0].id,
            foreign_tenant_id=foreign_tenant_id,
            foreign_user_id=users[1].id,
        )
    try:
        yield scope
    finally:
        await _purge_tenant(auth.tenant_id)
        await _purge_tenant(foreign_tenant_id)


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
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id)
            .order_by(ChatSession.issued_at)
        )
        return list(rows)


async def _audit_rows(tenant_id: str) -> list[ConfigAuditLog]:
    """本租户的 Token 审计行（同租户还有 LOGIN 等审计，按资源类型收敛）。"""
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ConfigAuditLog)
            .where(
                ConfigAuditLog.tenant_id == tenant_id,
                ConfigAuditLog.resource_type == AUDIT_RESOURCE_TYPE,
            )
            .order_by(ConfigAuditLog.create_time)
        )
        return list(rows)


async def _receipt_count(tenant_id: str) -> int:
    """本租户在两张真实幂等回执表里的行数：API-03 不得写任何可回放回执。"""
    total = 0
    async with get_session_factory()() as session:
        for table in RECEIPT_TABLES:
            statement = text(f"SELECT count(*) FROM control.{table} WHERE tenant_id = :tenant_id")
            total += int(await session.scalar(statement, {"tenant_id": tenant_id}) or 0)
    return total


def _row_dump(row: Any) -> str:
    """整行（含摘要列）的文本快照：用于断言原文不在库内任何列。"""
    return json.dumps({column.key: str(getattr(row, column.key)) for column in row.__table__.columns})


def _audit_dump(rows: list[ConfigAuditLog]) -> str:
    return json.dumps(
        [{"action": row.action, "before": row.before_json, "after": row.after_json} for row in rows],
        default=str,
    )


async def _issue_via_service(tenant_id: str, user_id: uuid.UUID) -> str:
    """绕过本 API 直接为某用户签发（真实服务/真实事务）：构造"另一租户已有 Token"的事实。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=tenant_id,
            user_id=user_id,
            expected_revision=0,
            actor=AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.9"),
        )
        await session.commit()
        return issued.token.get_secret_value()


async def _bind_chat_session(token_row: ChatAccessToken) -> uuid.UUID:
    """为本版本 Token 建一条真实活跃 CHAT 会话：撤销必须同事务带走它。"""
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        bound = ChatSession(
            tenant_id=token_row.tenant_id,
            access_token_id=token_row.id,
            platform_user_id=token_row.platform_user_id,
            token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            issued_at=now,
            expires_at=now + timedelta(hours=12),
        )
        session.add(bound)
        await session.commit()
        return bound.id


async def _put(
    client: AsyncClient, headers: dict[str, str], user_id: uuid.UUID, expected_revision: int
) -> Response:
    return await client.put(_url(user_id), json={"expected_revision": expected_revision}, headers=headers)


async def _delete(
    client: AsyncClient, headers: dict[str, str], user_id: uuid.UUID, expected_revision: int | None
) -> Response:
    params = {} if expected_revision is None else {"expected_revision": expected_revision}
    return await client.delete(_url(user_id), params=params, headers=headers)


async def test_b239_only_admin_with_csrf_may_manage_the_token(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：整组仅 ADMIN（读也不放行）；ADMIN 的写请求仍必须携带正确 CSRF，拒绝不留行。"""
    scope = token_scope
    anon = await client.get(_url(scope.user_id), headers=tenant_headers(auth))
    assert anon.status_code == 401 and anon.json()["code"] == "UNAUTHORIZED"

    assert (await login(client, auth, auth.builder_username, BUILDER_PASSWORD)).status_code == 200
    builder = csrf_headers(client)
    for method, extra in (
        ("get", {}),
        ("put", {"json": {"expected_revision": 0}}),
        ("delete", {"params": {"expected_revision": 0}}),
    ):
        denied = await getattr(client, method)(
            _url(scope.user_id), headers={**tenant_headers(auth), **builder}, **extra
        )
        assert denied.status_code == 403, (method, denied.text)
        assert denied.json()["code"] == "FORBIDDEN"
    assert await _token_rows(scope.tenant_id) == [], "被拒请求不得留下任何 Token 行"

    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200
    admin = _manage_headers(auth, client)
    assert admin["X-CSRF-Token"]
    no_csrf = await _put(client, tenant_headers(auth), scope.user_id, 0)
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "FORBIDDEN"
    stale = await _delete(
        client, {**tenant_headers(auth), "X-CSRF-Token": f"{admin['X-CSRF-Token']}-stale"}, scope.user_id, 0
    )
    assert stale.status_code == 403 and stale.json()["code"] == "FORBIDDEN"
    assert await _token_rows(scope.tenant_id) == []

    assert (await client.get(_url(scope.user_id), headers=tenant_headers(auth))).status_code == 200, (
        "安全方法（GET）不需要 CSRF"
    )
    accepted = await _put(client, admin, scope.user_id, 0)
    assert accepted.status_code == 200, "带正确 CSRF 的 ADMIN 写请求正常受理"


async def test_b239_get_returns_metadata_only_and_never_crosses_tenants(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：GET 只元信息且 no-store；租户取管理员主体，别的租户的 Token 读作未配置。"""
    scope = token_scope
    foreign_token = await _issue_via_service(scope.foreign_tenant_id, scope.foreign_user_id)
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200

    fresh = await client.get(_url(scope.user_id), headers=tenant_headers(auth))
    assert fresh.status_code == 200, fresh.text
    assert fresh.headers["cache-control"] == "no-store", "Token 元信息属私有数据，不得被中间层缓存"
    assert fresh.json()["data"] == NOT_CONFIGURED
    assert set(fresh.json()["data"]) == METADATA_FIELDS

    foreign = await client.get(_url(scope.foreign_user_id), headers=tenant_headers(auth))
    assert foreign.status_code == 200
    assert foreign.json()["data"] == NOT_CONFIGURED, "外租户真有 Token，但本租户管理员只应读作未配置"
    assert foreign_token not in foreign.text
    forged = await client.get(
        _url(scope.foreign_user_id), headers={"X-Tenant-Id": scope.foreign_tenant_id}
    )
    assert forged.status_code == 200
    assert forged.json()["data"] == NOT_CONFIGURED, "浏览器可改写的租户头不得切换读到的 Token"

    issued = await _put(client, _manage_headers(auth, client), scope.user_id, 0)
    assert issued.status_code == 200, issued.text
    plaintext = issued.json()["data"]["token"]
    after = await client.get(_url(scope.user_id), headers=tenant_headers(auth))
    assert after.headers["cache-control"] == "no-store"
    assert set(after.json()["data"]) == METADATA_FIELDS, "GET 只元信息：多一个字段都是泄漏面"
    assert (after.json()["data"]["status"], after.json()["data"]["revision"]) == ("ACTIVE", 1)
    assert plaintext not in after.text and "token_hash" not in after.text


async def test_b239_first_issue_reveals_plaintext_once_without_any_receipt(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：首签响应 no-store、明文只出现一次；即使带幂等键也不写回执，重放只撞 REVISION_CONFLICT。"""
    scope = token_scope
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200
    headers = {**_manage_headers(auth, client), "Idempotency-Key": f"issue-{uuid.uuid4()}"}

    response = await _put(client, headers, scope.user_id, 0)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store", "签发明文响应必须 no-store"
    data = response.json()["data"]
    assert set(data) == ISSUED_FIELDS
    token = data["token"]
    assert isinstance(token, str) and len(token) >= MIN_TOKEN_CHARS
    assert (data["configured"], data["status"], data["revision"]) == (True, "ACTIVE", 1)
    assert response.text.count(token) == 1, "原文只允许在本次响应里出现一次"

    rows = await _token_rows(scope.tenant_id)
    assert len(rows) == 1 and rows[0].revision == 1 and rows[0].revoked_at is None
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert rows[0].token_hash == digest, "库里只存 SHA-256 摘要"
    assert token not in _row_dump(rows[0]), "库内任何列都不得出现原文"

    audits = await _audit_rows(scope.tenant_id)
    assert [row.action for row in audits] == ["CREATE"]
    assert all(row.resource_type == AUDIT_RESOURCE_TYPE for row in audits)
    assert token not in _audit_dump(audits) and digest not in _audit_dump(audits), "审计连摘要都不写"

    assert await _receipt_count(scope.tenant_id) == 0, "API-03 不得写任何幂等回执（明文可回放面）"

    replay = await _put(client, headers, scope.user_id, 0)
    assert replay.status_code == 409 and replay.json()["code"] == "REVISION_CONFLICT"
    assert token not in replay.text, "重放不回放明文"
    assert len(await _token_rows(scope.tenant_id)) == 1, "重放不得轮换出新 Token"
    assert await _receipt_count(scope.tenant_id) == 0


async def test_b239_reset_single_winner_never_rotates_on_stale_or_replayed_revision(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：`expected_revision` 单胜——过期版本与响应丢失的重放都不再轮换、不回放明文。"""
    scope = token_scope
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200
    headers = _manage_headers(auth, client)

    first = await _put(client, headers, scope.user_id, 0)
    assert first.status_code == 200, first.text
    first_token = first.json()["data"]["token"]
    stale = await _put(client, headers, scope.user_id, 0)
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    assert first_token not in stale.text

    reset = await _put(client, headers, scope.user_id, 1)
    assert reset.status_code == 200, reset.text
    second_token = reset.json()["data"]["token"]
    assert (reset.json()["data"]["revision"], reset.json()["data"]["status"]) == (2, "ACTIVE")
    assert second_token != first_token and reset.text.count(second_token) == 1

    rows = await _token_rows(scope.tenant_id)
    assert [row.revision for row in rows] == [1, 2], "撤销不删行，版本基准保留历史行"
    assert rows[0].revoked_at is not None, "重置在同一事务撤销旧行"
    assert rows[1].revoked_at is None and rows[1].token_hash == hashlib.sha256(
        second_token.encode()
    ).hexdigest()
    assert first_token not in _row_dump(rows[1]) and second_token not in _row_dump(rows[0])

    replay = await _put(client, headers, scope.user_id, 1)
    assert replay.status_code == 409 and replay.json()["code"] == "REVISION_CONFLICT"
    assert second_token not in replay.text and len(await _token_rows(scope.tenant_id)) == 2

    for revision in (0, 3):
        unknown = await _put(client, headers, scope.user_id, revision)
        assert unknown.status_code == 409 and unknown.json()["code"] == "REVISION_CONFLICT"
    current = await client.get(_url(scope.user_id), headers=tenant_headers(auth))
    assert (current.json()["data"]["revision"], current.json()["data"]["status"]) == (2, "ACTIVE")


async def test_b239_revoke_is_version_scoped_and_takes_sessions_in_transaction(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：DELETE 按版本幂等；撤销同事务带走关联会话；旧版本迟到重放不误伤后续新 Token。"""
    scope = token_scope
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200
    headers = _manage_headers(auth, client)

    issued = await _put(client, headers, scope.user_id, 0)
    assert issued.status_code == 200, issued.text
    rows = await _token_rows(scope.tenant_id)
    session_id = await _bind_chat_session(rows[0])

    missing = await _delete(client, headers, scope.user_id, None)
    assert missing.status_code == 422, "expected_revision 是必填的显式版本"

    noop = await _delete(client, headers, scope.user_id, 0)
    assert noop.status_code == 200, noop.text
    assert noop.headers["cache-control"] == "no-store"
    assert noop.json()["data"]["status"] == "ACTIVE", "版本 0 在已有签发行时是空操作"
    assert (await _session_rows(scope.tenant_id))[0].revoked_at is None

    revoked = await _delete(client, headers, scope.user_id, 1)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["status"] == "REVOKED"
    stamped = (await _token_rows(scope.tenant_id))[0]
    bound = (await _session_rows(scope.tenant_id))[0]
    assert bound.id == session_id
    assert bound.revoked_at == stamped.revoked_at, "关联会话必须与来源 Token 同事务同一时刻撤销"

    replay = await _delete(client, headers, scope.user_id, 1)
    assert replay.status_code == 200 and replay.json()["data"]["status"] == "REVOKED"
    assert (await _token_rows(scope.tenant_id))[0].revoked_at == stamped.revoked_at, "重放不改时间戳"

    reset = await _put(client, headers, scope.user_id, 1)
    assert reset.status_code == 200, reset.text
    assert reset.json()["data"]["revision"] == 2
    late = await _delete(client, headers, scope.user_id, 1)
    assert late.status_code == 200
    latest = (await _token_rows(scope.tenant_id))[1]
    assert (latest.revision, latest.revoked_at) == (2, None), "旧版本迟到撤销不得误伤新 Token"
    assert (await client.get(_url(scope.user_id), headers=tenant_headers(auth))).json()["data"][
        "status"
    ] == "ACTIVE"

    for target, code in ((scope.foreign_user_id, 404), (uuid.uuid4(), 404)):
        missing_user = await _delete(client, headers, target, 1)
        assert missing_user.status_code == code, missing_user.text
        assert missing_user.json()["code"] == "COMMON_NOT_FOUND"
    cross = await _put(client, headers, scope.foreign_user_id, 0)
    assert cross.status_code == 404 and cross.json()["code"] == "COMMON_NOT_FOUND"
    assert await _token_rows(scope.foreign_tenant_id) == []


async def test_b239_user_token_is_not_a_console_credential(
    client: AsyncClient, auth: AuthContext, token_scope: TokenScope
) -> None:
    """B-239：签发的用户 Token 不授予 Console 权限——既不能读管理端点，也不能管理 Token。"""
    scope = token_scope
    assert (await login(client, auth, auth.admin_username, ADMIN_PASSWORD)).status_code == 200
    issued = await _put(client, _manage_headers(auth, client), scope.user_id, 0)
    assert issued.status_code == 200, issued.text
    token = issued.json()["data"]["token"]

    bearer = {"Authorization": f"Bearer {token}"}
    read = await client.get(_url(scope.user_id), headers=bearer)
    assert read.status_code == 401 and read.json()["code"] == "UNAUTHORIZED"
    managed = _manage_headers(auth, client)
    write = await client.put(
        _url(scope.user_id), json={"expected_revision": 1}, headers={**bearer, **managed}
    )
    assert write.status_code == 401 and write.json()["code"] == "UNAUTHORIZED"
    listing = await client.get("/api/v1/users", headers=bearer)
    assert listing.status_code == 401 and listing.json()["code"] == "UNAUTHORIZED"
    for response in (read, write, listing):
        assert token not in response.text, "被拒响应不得回显凭据"

    client.cookies.set("muad_session", token, path="/")
    cookie_read = await client.get(_url(scope.user_id), headers=tenant_headers(auth))
    assert cookie_read.status_code == 401 and cookie_read.json()["code"] == "UNAUTHORIZED"
    assert len(await _token_rows(scope.tenant_id)) == 1, "越权请求不得改变任何 Token 状态"
