"""[S-08][E-07] 账号创建与登录成功的审计归因（design FEAT-08 / §2.5.1 RULE-10）。

真实边界：真实 Console ASGI 全栈（登录会话 + CSRF）+ 真实 PostgreSQL（`config_audit_log`
逐行回读）。同事务语义以「失败的创建请求不新增审计行」取证；脱敏以「请求带 password
而审计载荷无该键与明文」取证。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_BUILDER,
    ConsoleAccount,
    ConsoleSession,
)
from muad_console_platform.infrastructure.models.control import ConfigAuditLog

from tests.console_auth.conftest import (
    ADMIN_PASSWORD,
    BUILDER_PASSWORD,
    AuthContext,
    csrf_headers,
    login,
    tenant_headers,
)

NEW_PASSWORD = "console-created-password"


async def _audit_rows(resource_id: uuid.UUID) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        result = await session.scalars(
            sa.select(ConfigAuditLog).where(ConfigAuditLog.resource_id == resource_id)
        )
        return list(result.all())


async def _account_create_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        total = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ConfigAuditLog)
            .where(
                ConfigAuditLog.tenant_id == tenant_id,
                ConfigAuditLog.resource_type == "CONSOLE_ACCOUNT",
                ConfigAuditLog.action == "CREATE",
            )
        )
        return int(total or 0)


def _create_payload(username: str) -> dict[str, str]:
    return {
        "username": username,
        "display_name": "Audited Account",
        "password": NEW_PASSWORD,
        "role": "BUILDER",
    }


async def test_s08_account_create_and_login_write_audit_with_actor(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-08] 登录成功与账号创建各写一条 `CONSOLE_ACCOUNT` 审计，actor 归因正确。"""
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200

    login_rows = [row for row in await _audit_rows(auth.admin_id) if row.action == "LOGIN"]
    assert login_rows, "登录成功须写 CONSOLE_ACCOUNT/LOGIN 审计"
    assert {row.actor_user_id for row in login_rows} == {auth.admin_id}
    assert {row.resource_type for row in login_rows} == {"CONSOLE_ACCOUNT"}
    assert {row.tenant_id for row in login_rows} == {auth.tenant_id}
    assert all(row.trace_id for row in login_rows)
    assert all(row.source_ip for row in login_rows)

    username = f"audited-{uuid.uuid4()}"
    created = await client.post(
        "/api/v1/accounts",
        json=_create_payload(username),
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert created.status_code == 200
    new_id = uuid.UUID(created.json()["data"]["id"])

    create_rows = [row for row in await _audit_rows(new_id) if row.action == "CREATE"]
    assert len(create_rows) == 1, "账号创建须写恰好一条 CONSOLE_ACCOUNT/CREATE 审计"
    assert create_rows[0].actor_user_id == auth.admin_id, "actor 须为创建者，而非被创建的账号"
    assert create_rows[0].resource_type == "CONSOLE_ACCOUNT"
    assert create_rows[0].tenant_id == auth.tenant_id
    assert create_rows[0].before_json is None
    assert create_rows[0].after_json is not None


async def test_s08_failed_create_leaves_no_audit_row(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-08] 同事务：重名创建失败（409）不得新增 CREATE 审计行。"""
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    before = await _account_create_count(auth.tenant_id)

    duplicate = await client.post(
        "/api/v1/accounts",
        json=_create_payload(auth.builder_username),
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ACCOUNT_USERNAME_EXISTS"

    after = await _account_create_count(auth.tenant_id)
    assert after == before, "失败的创建请求不得留下审计行（审计须与业务变更同事务）"


async def test_e07_audit_payload_strips_sensitive_keys(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-07] 请求带 `password`，审计载荷中不得出现该键或其明文。"""
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200

    username = f"sealed-{uuid.uuid4()}"
    created = await client.post(
        "/api/v1/accounts",
        json=_create_payload(username),
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert created.status_code == 200
    new_id = uuid.UUID(created.json()["data"]["id"])

    rows = await _audit_rows(new_id)
    assert rows, "创建成功须留下审计行（否则脱敏断言空转）"
    payloads = [row.before_json for row in rows] + [row.after_json for row in rows]
    assert NEW_PASSWORD not in json.dumps(payloads, ensure_ascii=False), "审计不得含密码明文"
    for row in rows:
        for payload in (row.before_json, row.after_json):
            if isinstance(payload, dict):
                assert "password" not in payload
                assert "password_hash" not in payload


# ---- TASK-003：登录链（E-01/E-02/E-06/B-01/B-03）----


async def _account_state(username: str) -> ConsoleAccount | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(ConsoleAccount).where(ConsoleAccount.username == username)
        )


async def _session_count(account_id: uuid.UUID) -> int:
    async with get_session_factory()() as session:
        total = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ConsoleSession)
            .where(ConsoleSession.account_id == account_id)
        )
        return int(total or 0)


async def test_e01_wrong_password_increments_counter_without_session(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-01] 正确用户名 + 错误密码 → 401；不建会话、不发 Cookie；计数 +1。"""
    response = await login(client, auth, auth.builder_username, "definitely-wrong-password")
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert "muad_session" not in response.cookies
    assert "muad_csrf" not in response.cookies

    state = await _account_state(auth.builder_username)
    assert state is not None
    assert state.failed_attempts == 1
    assert await _session_count(auth.builder_id) == 0


async def test_e02_fifth_failure_locks_and_correct_password_gets_423(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-02] 连续 5 次失败 → 锁定并重置计数；锁定期内即使密码正确也 423。"""
    for attempt in range(5):
        failed = await login(client, auth, auth.builder_username, "definitely-wrong-password")
        assert failed.status_code == 401, f"第 {attempt + 1} 次失败应为 401"

    state = await _account_state(auth.builder_username)
    assert state is not None
    assert state.failed_attempts == 0, "达到阈值后计数应重置"
    assert state.locked_until is not None
    assert state.locked_until > datetime.now(UTC)

    locked = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert locked.status_code == 423
    assert locked.json()["code"] == "ACCOUNT_LOCKED"
    assert await _session_count(auth.builder_id) == 0


async def test_b01_fourth_failure_does_not_lock(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[B-01] 第 4 次失败计数为 4 且**不**锁定（第 5 次进入锁定见 E-02）。"""
    for expected in (1, 2, 3, 4):
        failed = await login(client, auth, auth.builder_username, "definitely-wrong-password")
        assert failed.status_code == 401
        state = await _account_state(auth.builder_username)
        assert state is not None
        assert state.failed_attempts == expected
        assert state.locked_until is None, f"第 {expected} 次失败不应锁定"


async def test_e06_disabled_account_is_indistinguishable_from_wrong_password(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-06] 禁用账号 + 正确密码 → 401 `INVALID_CREDENTIALS`（不暴露"已禁用"）。"""
    disabled = await _account_state(auth.disabled_username)
    assert disabled is not None and disabled.enabled is False

    response = await login(client, auth, auth.disabled_username, BUILDER_PASSWORD)
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert await _session_count(disabled.id) == 0


async def test_b03_cross_tenant_duplicate_username_requires_tenant_header(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[B-03] 跨租户同名：无 `X-Tenant-Id` 时全局解析歧义 → 401；带租户头时正确账号可登录。"""
    other_tenant = f"test-other-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        session.add(
            ConsoleAccount(
                tenant_id=other_tenant,
                username=auth.builder_username,
                display_name="Other Tenant Builder",
                password_hash=hash_password(BUILDER_PASSWORD),
                role=ROLE_BUILDER,
            )
        )
        await session.commit()
    try:
        ambiguous = await client.post(
            "/api/v1/auth/login",
            json={"username": auth.builder_username, "password": BUILDER_PASSWORD},
        )
        assert ambiguous.status_code == 401
        assert ambiguous.json()["code"] == "INVALID_CREDENTIALS"

        scoped = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
        assert scoped.status_code == 200
        assert scoped.json()["data"]["username"] == auth.builder_username
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                sa.delete(ConsoleAccount).where(ConsoleAccount.tenant_id == other_tenant)
            )
            await session.commit()
