"""[S-08][E-07] 账号创建与登录成功的审计归因（design FEAT-08 / §2.5.1 RULE-10）。

真实边界：真实 Console ASGI 全栈（登录会话 + CSRF）+ 真实 PostgreSQL（`config_audit_log`
逐行回读）。同事务语义以「失败的创建请求不新增审计行」取证；脱敏以「请求带 password
而审计载荷无该键与明文」取证。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import sqlalchemy as sa
from httpx import AsyncClient
from muad_common import SharedSettings
from muad_console_platform.application.auth_service import AuthService, hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
    ConsoleSession,
)
from muad_console_platform.infrastructure.models.control import ConfigAuditLog
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.console_auth_flow.environment import (
    _base_env,
    stop_auth_stack,
    wait_ready,
)
from tests.acceptance.task_schedule.environment import (
    ServiceProcess,
    clear_engine_caches,
    free_port,
    require,
    run_db,
)
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


# ---- TASK-004：会话链（S-02/E-03/B-02）----

# 边界取样用 ±5s 而非"恰好"：会话在请求前写入、请求后回读，wall-clock 必然流逝；
# 若取恰好 6h，读回时剩余必已 <6h 而触发续期，断言会变成与执行速度耦合的 flaky 用例。
BOUNDARY_ABOVE = timedelta(hours=6, seconds=5)
BOUNDARY_BELOW = timedelta(hours=6, seconds=-5)


async def _issue_session(account_id: uuid.UUID, *, remaining: timedelta) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        session.add(
            ConsoleSession(
                account_id=account_id,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                issued_at=now,
                expires_at=now + remaining,
                last_seen_at=now,
            )
        )
        await session.commit()
    return token


async def _session_state(token: str) -> ConsoleSession | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(ConsoleSession).where(ConsoleSession.token_hash == digest)
        )


async def _me(client: AsyncClient, auth: AuthContext, token: str):
    # 用显式 Cookie 头而非 per-request cookies=（后者已被 httpx 标记弃用）
    return await client.get(
        "/api/v1/auth/me",
        headers={**tenant_headers(auth), "Cookie": f"muad_session={token}"},
    )


async def test_s02_sliding_renewal_extends_expiry_when_under_half(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-02] 剩余 <6h 的会话调 `/auth/me` → 续期为 now+12h 且 `last_seen_at` 更新。"""
    token = await _issue_session(auth.builder_id, remaining=timedelta(hours=3))
    before = await _session_state(token)
    assert before is not None

    response = await _me(client, auth, token)
    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(auth.builder_id)

    after = await _session_state(token)
    assert after is not None
    assert after.expires_at > before.expires_at, "剩余 <6h 必须续期"
    remaining_after = after.expires_at - datetime.now(UTC)
    assert timedelta(hours=11) < remaining_after <= timedelta(hours=12), "续期目标应为 now+12h"
    assert after.last_seen_at >= before.last_seen_at


async def test_e03_expired_revoked_and_unknown_tokens_are_unauthorized(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-03] 过期、已撤销、未知令牌访问 `/me` 一律 401 `UNAUTHORIZED`，封套含 `trace_id`。"""
    expired = await _issue_session(auth.builder_id, remaining=timedelta(hours=-1))
    revoked = await _issue_session(auth.builder_id, remaining=timedelta(hours=3))
    digest = hashlib.sha256(revoked.encode("utf-8")).hexdigest()
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(ConsoleSession).where(ConsoleSession.token_hash == digest)
        )
        assert row is not None
        row.revoked_at = datetime.now(UTC)
        await session.commit()

    for label, token in (("过期", expired), ("已撤销", revoked), ("未知", "unknown-token-xyz")):
        response = await _me(client, auth, token)
        assert response.status_code == 401, f"{label}令牌应为 401"
        body = response.json()
        assert body["code"] == "UNAUTHORIZED", label
        assert body["trace_id"], f"{label}令牌的封套须含 trace_id"


async def test_b02_six_hour_boundary_renews_only_below(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[B-02] 剩余略多于 6h **不**续期；略少于 6h **续期**（阈值取临界两侧，见上方取样说明）。"""
    above = await _issue_session(auth.builder_id, remaining=BOUNDARY_ABOVE)
    before_above = await _session_state(above)
    assert before_above is not None
    assert (await _me(client, auth, above)).status_code == 200
    after_above = await _session_state(above)
    assert after_above is not None
    assert after_above.expires_at == before_above.expires_at, "剩余 >6h 不应续期"
    assert after_above.last_seen_at >= before_above.last_seen_at, "但仍应更新活跃时间"

    below = await _issue_session(auth.builder_id, remaining=BOUNDARY_BELOW)
    before_below = await _session_state(below)
    assert before_below is not None
    assert (await _me(client, auth, below)).status_code == 200
    after_below = await _session_state(below)
    assert after_below is not None
    assert after_below.expires_at > before_below.expires_at, "剩余 <6h 应续期"


# ---- TASK-005：登出撤销（S-03 的集成侧证据）----


async def test_s03_logout_revokes_session_and_blocks_reuse(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-03] 带 CSRF 登出 → 200 `{logged_out:true}`；库内 `revoked_at` 置位；旧令牌再访问 401。"""
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200
    token = client.cookies.get("muad_session")
    csrf = client.cookies.get("muad_csrf")
    assert token and csrf

    logged_out = await client.post(
        "/api/v1/auth/logout",
        headers={**tenant_headers(auth), "X-CSRF-Token": csrf},
    )
    assert logged_out.status_code == 200
    assert logged_out.json()["data"]["logged_out"] is True

    state = await _session_state(token)
    assert state is not None
    assert state.revoked_at is not None, "登出必须置 revoked_at"

    reuse = await client.get(
        "/api/v1/auth/me", headers={**tenant_headers(auth), "Cookie": f"muad_session={token}"}
    )
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "UNAUTHORIZED"


async def test_s03_logout_is_idempotent_at_service_layer(auth: AuthContext) -> None:
    """[S-03] 服务层幂等：已撤销/未知/空令牌调用 `logout` 直接返回，不抛错。

    **边界说明**：API 层的 `logout` 路由位于认证组，重复登出会先被会话校验挡下（401），
    故「不存在或已撤销直接成功」的幂等语义只能在 **service 层**取证——design §3.4 描述的
    正是该层行为。
    """
    async with get_session_factory()() as session:
        service = AuthService(session, tenant_id=auth.tenant_id)
        await service.logout("unknown-token-xyz")
        await service.logout("")
        await session.commit()


# ---- TASK-006：修改密码（S-04 的集成侧证据）----

ROTATED_PASSWORD = "console-rotated-password-123"


async def test_s04_change_password_rotates_hash_and_invalidates_old_password(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-04] 改密成功 → `{changed:true}`；哈希轮换为 argon2id；旧密码失效、新密码可用。"""
    before = await _account_state(auth.builder_username)
    assert before is not None

    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200
    csrf = client.cookies.get("muad_csrf")
    assert csrf

    changed = await client.post(
        "/api/v1/auth/password",
        json={"current_password": BUILDER_PASSWORD, "new_password": ROTATED_PASSWORD},
        headers={**tenant_headers(auth), "X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["changed"] is True

    after = await _account_state(auth.builder_username)
    assert after is not None
    assert after.password_hash != before.password_hash, "哈希必须轮换"
    assert after.password_hash.startswith("$argon2id$"), "必须存 argon2id 哈希"

    # design §2.4 技术债③：V1 不撤销其他既有会话——既有会话仍可用
    me = await client.get("/api/v1/auth/me", headers=tenant_headers(auth))
    assert me.status_code == 200, "改密不应撤销既有会话（V1 既定行为）"

    old = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert old.status_code == 401, "旧密码必须失效"
    new = await login(client, auth, auth.builder_username, ROTATED_PASSWORD)
    assert new.status_code == 200, "新密码必须可用"


async def test_s04_short_new_password_and_wrong_current_password_are_rejected(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[S-04] 边界：新密码 <12 位 → 422；当前密码错误 → 401；失败路径不改动密码。"""
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200
    csrf = client.cookies.get("muad_csrf")
    assert csrf
    headers = {**tenant_headers(auth), "X-CSRF-Token": csrf}

    short = await client.post(
        "/api/v1/auth/password",
        json={"current_password": BUILDER_PASSWORD, "new_password": "too-short"},
        headers=headers,
    )
    assert short.status_code == 422
    assert short.json()["code"] == "COMMON_VALIDATION_ERROR"

    wrong = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "definitely-not-the-password", "new_password": ROTATED_PASSWORD},
        headers=headers,
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"

    still = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert still.status_code == 200, "失败路径不得改变密码"


# ---- TASK-007：账号管理与访问控制（E-04/E-05/E-08/B-08）----


async def _tenant_account_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        total = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ConsoleAccount)
            .where(ConsoleAccount.tenant_id == tenant_id, ConsoleAccount.is_deleted.is_(False))
        )
        return int(total or 0)


async def test_e04_missing_or_forged_csrf_is_forbidden(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-04] 非安全方法缺失或伪造 `X-CSRF-Token` → 403 `FORBIDDEN`，且不落业务变更。"""
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    before = await _account_create_count(auth.tenant_id)

    missing = await client.post(
        "/api/v1/accounts",
        json=_create_payload(f"csrf-{uuid.uuid4()}"),
        headers=tenant_headers(auth),
    )
    assert missing.status_code == 403
    assert missing.json()["code"] == "FORBIDDEN"

    forged = await client.post(
        "/api/v1/accounts",
        json=_create_payload(f"csrf-{uuid.uuid4()}"),
        headers={**tenant_headers(auth), "X-CSRF-Token": "forged-token"},
    )
    assert forged.status_code == 403
    assert forged.json()["code"] == "FORBIDDEN"

    assert await _account_create_count(auth.tenant_id) == before, "CSRF 失败不得落业务变更"


async def test_e05_builder_cannot_reach_accounts(client: AsyncClient, auth: AuthContext) -> None:
    """[E-05] BUILDER 访问 `/api/v1/accounts`（读与写）→ 403，且不发生写入。"""
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200
    before = await _account_create_count(auth.tenant_id)

    listing = await client.get("/api/v1/accounts", headers=tenant_headers(auth))
    assert listing.status_code == 403
    assert listing.json()["code"] == "FORBIDDEN"

    creating = await client.post(
        "/api/v1/accounts",
        json=_create_payload(f"rbac-{uuid.uuid4()}"),
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert creating.status_code == 403
    assert await _account_create_count(auth.tenant_id) == before


async def test_e08_unauthenticated_rejected_while_public_routes_stay_open(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[E-08] 未登录访问受保护端点 → 401；`/healthz` 与 `/auth/login` 保持公开。"""
    for path in ("/api/v1/agents", "/api/v1/accounts"):
        response = await client.get(path, headers=tenant_headers(auth))
        assert response.status_code == 401, path
        assert response.json()["code"] == "UNAUTHORIZED", path

    health = await client.get("/healthz")
    assert health.status_code == 200

    public_login = await client.post(
        "/api/v1/auth/login",
        json={"username": auth.admin_username, "password": ADMIN_PASSWORD},
        headers=tenant_headers(auth),
    )
    assert public_login.status_code == 200


async def test_b08_account_creation_is_idempotent_by_key(
    client: AsyncClient, auth: AuthContext
) -> None:
    """[B-08] 同 key 同指纹重放返回同一账号且不重复创建；异指纹 `IDEMPOTENCY_MISMATCH`。"""
    response = await login(client, auth, auth.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200
    key = f"acct-key-{uuid.uuid4()}"
    payload = _create_payload(f"idem-{uuid.uuid4()}")
    headers = {**tenant_headers(auth), **csrf_headers(client), "Idempotency-Key": key}
    accounts_before = await _tenant_account_count(auth.tenant_id)
    audits_before = await _account_create_count(auth.tenant_id)

    first = await client.post("/api/v1/accounts", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    first_id = first.json()["data"]["id"]
    assert await _tenant_account_count(auth.tenant_id) == accounts_before + 1
    assert await _account_create_count(auth.tenant_id) == audits_before + 1

    replay = await client.post("/api/v1/accounts", json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == first_id, "同 key 同指纹必须重放首次结果"
    assert await _tenant_account_count(auth.tenant_id) == accounts_before + 1, "重放不得重复建号"
    assert await _account_create_count(auth.tenant_id) == audits_before + 1, "重放不产生新变更、不写新审计"

    mismatch = await client.post(
        "/api/v1/accounts", json={**payload, "role": "ADMIN"}, headers=headers
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await _tenant_account_count(auth.tenant_id) == accounts_before + 1


# ---- TASK-008：CLI create-admin 与启动自检（S-07）----


CONSOLE_SERVICE = "muad-console-platform"
NO_ACCOUNT_WARNING = "console_no_accounts_run_cli_create_admin"
SKIPPED_WARNING = "console_account_check_skipped_database_url_missing"
FAILED_WARNING = "console_account_check_failed"
CLI_ADMIN_PASSWORD = "console-cli-admin-password"
REPO_ROOT = Path(__file__).resolve().parents[3]
SELF_CHECK_SNIPPET = (
    "import asyncio; from muad_console_platform.main import _warn_if_no_accounts; "
    "asyncio.run(_warn_if_no_accounts())"
)


def _service_log_path(log_dir: Path) -> Path:
    """logging-kit 的落盘约定：`{LOG_DIR}/{service}/{YYYY-MM-DD}.log`。"""
    return log_dir / CONSOLE_SERVICE / f"{datetime.now().astimezone():%Y-%m-%d}.log"


def _service_log_records(log_dir: Path) -> list[dict[str, object]]:
    """只读 logging-kit 的结构化落盘文件（**不含**子进程 stdout 捕获文件），逐行解析 JSON。"""
    path = _service_log_path(log_dir)
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:  # 非 JSON 行（如 exception 字段外的续行）不计入记录
            continue
    return records


async def _wait_for_record(log_dir: Path, message: str, timeout: float = 20.0) -> dict[str, object] | None:
    """轮询等待落盘记录出现；超时返回 None（由调用方给出可诊断的断言信息）。"""
    deadline = time.monotonic() + timeout
    while True:
        for record in _service_log_records(log_dir):
            if record.get("message") == message:
                return record
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.3)


def _console_env(database_url: str, redis_url: str, root: Path, log_dir: Path, tenant: str) -> dict[str, str]:
    """真实 Console 子进程环境：复用验收栈基线 env，再钉死 `LOG_DIR` 与默认租户。"""
    return {
        **_base_env(database_url, redis_url, root),
        "LOG_DIR": str(log_dir),
        "DEFAULT_TENANT_ID": tenant,
    }


def _run_cli(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "muad_console_platform.cli", *args],
        cwd=str(REPO_ROOT),
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )


def _run_self_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """真实子进程直接驱动 lifespan 自检函数：真实 logging 落盘，无 mock。"""
    return subprocess.run(
        [sys.executable, "-c", SELF_CHECK_SNIPPET],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


async def _account_exists(username: str) -> bool:
    async with get_session_factory()() as session:
        found = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ConsoleAccount)
            .where(ConsoleAccount.username == username)
        )
        return int(found or 0) > 0


async def test_s07_cli_create_admin_and_startup_warning(tmp_path: Path) -> None:
    """[S-07] 该租户无账号启动 → 告警落盘；`create-admin` 建档 → 重启告警消失且新账号可登录。

    缺席断言的可信度来自同构对照：两次启动的进程形态、环境、租户完全一致，唯一差异是「该租户
    是否已有账号」；第一次已证明这条落盘链路会产出该记录，重启前又先回读账号数 = 1，故第二次
    的「无记录」可归因于账号存在，而非链路静默失效。
    """

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)
    tenant = f"auth-cli-{uuid.uuid4()}"
    username = f"cli-admin-{uuid.uuid4().hex[:8]}"
    logs_before = tmp_path / "logs-before"
    logs_after = tmp_path / "logs-after"
    logs_before.mkdir(parents=True, exist_ok=True)
    logs_after.mkdir(parents=True, exist_ok=True)
    processes: list[ServiceProcess] = []
    try:
        # 1) 该租户尚无账号：真实 lifespan 自检必须把告警写进 logging-kit 的落盘文件
        first = ServiceProcess(
            name="console-s07-before",
            module="muad_console_platform.main",
            port=free_port(),
            env=_console_env(database_url, redis_url, tmp_path, logs_before, tenant),
            log_path=tmp_path / "console-s07-before.stdout.log",
        )
        processes.append(first)
        first.start()
        await wait_ready(f"{first.url}/healthz")
        assert await _tenant_account_count(tenant) == 0, "前置：该租户必须无账号"
        record = await _wait_for_record(logs_before, NO_ACCOUNT_WARNING)
        assert record is not None, (
            f"{_service_log_path(logs_before)} 未记录无账号告警；"
            f"实际记录={_service_log_records(logs_before)}"
        )
        assert record["level"] == "WARNING"
        assert record["service"] == CONSOLE_SERVICE
        first.stop()

        # 2) CLI 建档：exit 0 且 stdout 文案与 design 完全一致
        created = _run_cli(
            "create-admin",
            "--username",
            username,
            "--password",
            CLI_ADMIN_PASSWORD,
            "--role",
            "ADMIN",
            "--tenant",
            tenant,
            database_url=database_url,
        )
        assert created.returncode == 0, created.stderr
        assert created.stdout.strip() == f"created console account {username} role=ADMIN tenant={tenant}"
        assert await _tenant_account_count(tenant) == 1

        # 3) 短密码：exit 2 且不发起 DB 写入
        short_username = f"cli-short-{uuid.uuid4().hex[:8]}"
        short = _run_cli(
            "create-admin",
            "--username",
            short_username,
            "--password",
            "short",
            "--tenant",
            tenant,
            database_url=database_url,
        )
        assert short.returncode == 2, short.stderr
        assert "at least 12 characters" in short.stderr
        assert await _account_exists(short_username) is False, "短密码不得写库"

        # 4) 建档后重启：告警消失（同构对照），且新账号真实可登录
        second = ServiceProcess(
            name="console-s07-after",
            module="muad_console_platform.main",
            port=free_port(),
            env=_console_env(database_url, redis_url, tmp_path, logs_after, tenant),
            log_path=tmp_path / "console-s07-after.stdout.log",
        )
        processes.append(second)
        second.start()
        await wait_ready(f"{second.url}/healthz")
        await asyncio.sleep(3.0)  # 缺席宽限期：覆盖实测落盘延迟
        assert await _tenant_account_count(tenant) == 1, "缺席断言的前提：该租户此时已有账号"
        messages = [entry.get("message") for entry in _service_log_records(logs_after)]
        assert NO_ACCOUNT_WARNING not in messages, f"已有账号时不得再告警：{messages}"

        async with httpx.AsyncClient(base_url=second.url, timeout=10.0) as client:
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": CLI_ADMIN_PASSWORD},
                headers={"X-Tenant-Id": tenant},
            )
        assert login_response.status_code == 200, login_response.text
        assert login_response.json()["data"]["role"] == "ADMIN"
    finally:
        stop_auth_stack(processes)
        clear_engine_caches()
        _purge_tenant(tenant)


async def test_s07_cli_defaults_to_admin_role_and_default_tenant() -> None:
    """[S-07] 省略 `--role`/`--tenant` 时分别默认 ADMIN 与 default，stdout 文案与 design 一致。"""

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    username = f"cli-default-{uuid.uuid4().hex[:8]}"
    try:
        created = _run_cli(
            "create-admin", "--username", username, "--password", CLI_ADMIN_PASSWORD,
            database_url=database_url,
        )
        assert created.returncode == 0, created.stderr
        assert created.stdout.strip() == f"created console account {username} role=ADMIN tenant=default"
        async with get_session_factory()() as session:
            account = await session.scalar(
                sa.select(ConsoleAccount).where(ConsoleAccount.username == username)
            )
        assert account is not None, "CLI 必须真实写库"
        assert account.tenant_id == "default"
        assert account.role == ROLE_ADMIN
    finally:
        clear_engine_caches()
        _purge_account(username)


def test_s07_self_check_skips_without_database_url(tmp_path: Path) -> None:
    """[S-07] 无 `DATABASE_URL` 时记录 `console_account_check_skipped_database_url_missing`（不静默）。

    该分支在真实 lifespan 中不可达——`validate_startup` 会先以 `database_url is not configured`
    阻断启动，故以真实子进程直接驱动自检函数，落盘仍是真实 logging-kit。
    """

    log_dir = tmp_path / "logs-skipped"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = _run_self_check({**os.environ, "LOG_DIR": str(log_dir), "DATABASE_URL": ""})
    assert result.returncode == 0, result.stderr
    assert [entry.get("message") for entry in _service_log_records(log_dir)] == [SKIPPED_WARNING]


def test_s07_self_check_logs_query_failure(tmp_path: Path) -> None:
    """[S-07] 查询异常记录 `console_account_check_failed`（含堆栈，不静默吞错）。

    边界取「库可达但 `control.console_account` 不存在」（真实 PostgreSQL 系统库 `postgres`）：
    连接级失败（端口不可达、库不存在）在 `validate_startup` 阶段即已阻断启动，lifespan 走不到
    自检；且 asyncpg 的连接异常不被 SQLAlchemy 包装成 `SQLAlchemyError`，只有查询期异常走此分支。
    """

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    schemaless_url = (
        make_url(database_url).set(database="postgres").render_as_string(hide_password=False)
    )
    log_dir = tmp_path / "logs-failed"
    log_dir.mkdir(parents=True, exist_ok=True)
    result = _run_self_check({**os.environ, "LOG_DIR": str(log_dir), "DATABASE_URL": schemaless_url})
    assert result.returncode == 0, result.stderr
    records = _service_log_records(log_dir)
    assert [entry.get("message") for entry in records] == [FAILED_WARNING]
    assert records[0]["level"] == "ERROR"
    assert "UndefinedTableError" in str(records[0].get("exception", "")), "必须保留真实异常上下文"


def _purge_tenant(tenant: str) -> None:
    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            for statement in (
                "DELETE FROM control.console_session WHERE account_id IN "
                "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
                "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
                "DELETE FROM control.console_account WHERE tenant_id = :t",
            ):
                await session.execute(sa.text(statement), {"t": tenant})
            await session.commit()

    run_db(purge)


def _purge_account(username: str) -> None:
    """按用户名精确清理：`default` 是共享租户，不能用租户级清理。"""

    async def purge(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            await session.execute(
                sa.text(
                    "DELETE FROM control.console_session WHERE account_id IN "
                    "(SELECT id FROM control.console_account WHERE username = :u)"
                ),
                {"u": username},
            )
            await session.execute(
                sa.text("DELETE FROM control.console_account WHERE username = :u"), {"u": username}
            )
            await session.commit()

    run_db(purge)
