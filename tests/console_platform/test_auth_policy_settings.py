"""[E-15] 认证策略接入平台设置：真实 PostgreSQL + 真实登录会话与 CSRF。

真实边界（业务路径不 mock）：
- **真实 PostgreSQL**：`control.platform_setting` 写的是真行，会话/账号落 `control.console_session`
  / `console_account`；会话时长改的是真列 `expires_at`。
- **真实登录会话与 CSRF**：账号创建经 `POST /api/v1/accounts`（真实 admin 会话 + `X-CSRF-Token`），
  语言/会话经 `POST /api/v1/auth/login` 的真实 Set-Cookie（`muad_session` / `muad_csrf`）。
- 平台设置经 `PlatformSettingsService.save` 真实落库（与 API-02 同一条服务路径）。

覆盖断言（design §2.5.2 E-15）：
1. 新签发会话按新 `session_ttl_hours`：`expires_at - issued_at` 与 Cookie `Max-Age` **同源**；
2. 已签发会话的到期时间不因改设置而变（只影响之后签发/续期的会话）；
3. 续期（滑动）按**当前** TTL，且阈值仍是派生值（`SESSION_TTL/2`）；
4. `max_failed_attempts` / `lock_duration_minutes` 由设置提供；
5. `min_password_length` 由设置提供且**更严**时新密码被拒；存量密码不回改。

清库隔离：`console_platform/conftest.py` 的 `tenant` 清理不含 `platform_setting` /
`console_account`，故本文件自带夹具在用例结束时删掉本租户的这些行。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.auth_service import hash_password, hash_session_token
from muad_console_platform.application.platform_settings_service import PlatformSettingsService
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ConsoleAccount,
    ConsoleSession,
)
from muad_console_platform.main import app
from muad_contracts.platform_settings import AuthSettings, default_platform_settings
from sqlalchemy import text

from console_platform.conftest import TenantContext

ADMIN_PASSWORD = "console-admin-password"


async def _save_auth(tenant_id: str, **overrides: int) -> None:
    """把 `auth` 分组的部分叶子写入平台设置（真实落库，revision 递增）。"""
    factory = get_session_factory()
    async with factory() as session:
        service = PlatformSettingsService(session)
        revision = (await service.read_current(tenant_id)).revision
        settings = replace(default_platform_settings(), auth=replace(AuthSettings(), **overrides))
        await service.save(tenant_id, AuditActor(account_id=uuid.uuid4()), revision, settings)
        await session.commit()


async def _fetch_session(token: str) -> ConsoleSession:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(
            sa.select(ConsoleSession).where(ConsoleSession.token_hash == hash_session_token(token))
        )
    assert row is not None
    return row


async def _fetch_account(tenant_id: str, username: str) -> ConsoleAccount:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(
            sa.select(ConsoleAccount).where(
                ConsoleAccount.tenant_id == tenant_id, ConsoleAccount.username == username
            )
        )
    assert row is not None
    return row


@pytest.fixture
async def policy_env(tenant: TenantContext) -> AsyncIterator[dict[str, str]]:
    """本用例租户 + 一个 ADMIN 账号；结束清理平台设置与账号/会话行。"""
    tenant_id = tenant.tenant_id
    admin_username = f"policy-admin-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ConsoleAccount(
                tenant_id=tenant_id,
                username=admin_username,
                display_name="Policy Admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
            )
        )
        await session.commit()
    try:
        yield {"tenant_id": tenant_id, "admin_username": admin_username}
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "DELETE FROM control.console_session WHERE account_id IN "
                    "(SELECT id FROM control.console_account WHERE tenant_id = :tenant_id)"
                ),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM control.console_account WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await session.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _login(client: AsyncClient, env: dict[str, str], password: str = ADMIN_PASSWORD) -> Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"username": env["admin_username"], "password": password},
        headers={"X-Tenant-Id": env["tenant_id"]},
    )


async def test_new_session_uses_configured_ttl_and_cookie_max_age(
    client: AsyncClient, policy_env: dict[str, str]
) -> None:
    """[E-15] 新签发会话按设置 TTL；Cookie `Max-Age` 与 `expires_at - issued_at` 同源。"""
    await _save_auth(policy_env["tenant_id"], session_ttl_hours=3)

    response = await _login(client, policy_env)
    assert response.status_code == 200, response.text

    # Cookie 与行同源：两枚 cookie 的 Max-Age 都等于 3h=10800s。
    cookie_headers = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{SESSION_COOKIE}=") or value.startswith(f"{CSRF_COOKIE}=")
    ]
    assert len(cookie_headers) == 2, cookie_headers
    assert all("Max-Age=10800" in value for value in cookie_headers), cookie_headers

    token = client.cookies.get(SESSION_COOKIE)
    assert token
    row = await _fetch_session(token)
    assert row.expires_at - row.issued_at == timedelta(hours=3)


async def test_existing_session_expiry_unchanged_when_ttl_changes(
    client: AsyncClient, policy_env: dict[str, str]
) -> None:
    """[E-15] 改会话时长只影响之后签发/续期的会话：已签发会话的到期时间不变。"""
    logged_in = await _login(client, policy_env)
    assert logged_in.status_code == 200, logged_in.text
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    original_expiry = (await _fetch_session(token)).expires_at

    await _save_auth(policy_env["tenant_id"], session_ttl_hours=48)

    assert (await _fetch_session(token)).expires_at == original_expiry


async def test_session_slide_uses_current_ttl_with_derived_threshold(
    client: AsyncClient, policy_env: dict[str, str]
) -> None:
    """[E-15] 续期按**当前** TTL；阈值仍是派生值（本会话签发 TTL/2，非独立设置项）。"""
    await _save_auth(policy_env["tenant_id"], session_ttl_hours=48)

    account = await _fetch_account(policy_env["tenant_id"], policy_env["admin_username"])
    now = datetime.now(UTC)
    token = "slide-" + uuid.uuid4().hex
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            ConsoleSession(
                account_id=account.id,
                token_hash=hash_session_token(token),
                # 签发 TTL 48h ⇒ 阈值 24h；剩余 1h < 24h ⇒ 触发续期，续期后到期 = now + 当前 48h。
                issued_at=now - timedelta(hours=47),
                expires_at=now + timedelta(hours=1),
                last_seen_at=now - timedelta(hours=47),
            )
        )
        await session.commit()

    client.cookies.set(SESSION_COOKIE, token)
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text

    row = await _fetch_session(token)
    assert row.expires_at > datetime.now(UTC) + timedelta(hours=47)

    # 未到续期点（签发 TTL 30h ⇒ 阈值 15h；剩余 30h ≥ 15h）⇒ 到期时间不变，且不读设置。
    untouched_token = "slide-" + uuid.uuid4().hex
    async with factory() as session:
        session.add(
            ConsoleSession(
                account_id=account.id,
                token_hash=hash_session_token(untouched_token),
                issued_at=now,
                expires_at=now + timedelta(hours=30),
                last_seen_at=now,
            )
        )
        await session.commit()
    client.cookies.set(SESSION_COOKIE, untouched_token)
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await _fetch_session(untouched_token)).expires_at == now + timedelta(hours=30)


async def test_failed_attempts_and_lock_duration_from_settings(
    client: AsyncClient, policy_env: dict[str, str]
) -> None:
    """[E-15] `max_failed_attempts` / `lock_duration_minutes` 由设置提供，服务端校验为准。"""
    await _save_auth(policy_env["tenant_id"], max_failed_attempts=2, lock_duration_minutes=30)

    for _ in range(2):
        failure = await _login(client, policy_env, password="wrong-password")
        assert failure.json()["code"] == "INVALID_CREDENTIALS"

    locked = await _login(client, policy_env, password="wrong-password")
    assert locked.json()["code"] == "ACCOUNT_LOCKED"

    account = await _fetch_account(policy_env["tenant_id"], policy_env["admin_username"])
    assert account.locked_until is not None
    remaining = account.locked_until - datetime.now(UTC)
    assert timedelta(minutes=29) < remaining <= timedelta(minutes=30)


async def test_password_length_from_settings_and_existing_password_unchanged(
    client: AsyncClient, policy_env: dict[str, str]
) -> None:
    """[E-15] 新密码按新长度校验（更严即拒）；存量密码不回改，仍可登录。"""
    legacy_username = f"legacy-{uuid.uuid4()}"
    legacy_password = "twelve-chars"  # 12 位：默认策略下合法

    logged_in = await _login(client, policy_env)
    assert logged_in.status_code == 200, logged_in.text
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf
    headers = {CSRF_HEADER: csrf, "X-Tenant-Id": policy_env["tenant_id"]}

    created = await client.post(
        "/api/v1/accounts",
        json={
            "username": legacy_username,
            "display_name": "Legacy",
            "password": legacy_password,
            "role": "BUILDER",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text

    # 收紧到 20：12 位新密码被服务端拒绝（400 业务校验，非 DTO 形状校验）。
    await _save_auth(policy_env["tenant_id"], min_password_length=20)
    rejected = await client.post(
        "/api/v1/accounts",
        json={
            "username": f"short-{uuid.uuid4()}",
            "display_name": "Short",
            "password": legacy_password,
            "role": "BUILDER",
        },
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "COMMON_BAD_REQUEST"

    accepted = await client.post(
        "/api/v1/accounts",
        json={
            "username": f"long-{uuid.uuid4()}",
            "display_name": "Long",
            "password": "twenty-char-password",  # 20 位
            "role": "BUILDER",
        },
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text

    # 存量密码不回改：收紧策略前建的 12 位密码仍能登录。
    fresh = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with fresh:
        still_valid = await fresh.post(
            "/api/v1/auth/login",
            json={"username": legacy_username, "password": legacy_password},
            headers={"X-Tenant-Id": policy_env["tenant_id"]},
        )
    assert still_valid.status_code == 200, still_valid.text
