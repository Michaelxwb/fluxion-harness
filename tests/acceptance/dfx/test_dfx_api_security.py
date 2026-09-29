"""[E-08][RULE-auth-001] API 安全与租户隔离验收（integration）。

真实边界：**真实 Console uvicorn 子进程**（真实 HTTP、真实 Cookie/CSRF 中间件、真实会话解析）
+ **真实 PostgreSQL**（账号/会话/审计/资源行一律逐行回读，不以返回值或日志代替）。本层不 mock 任何边界。

覆盖（E-08）：缺失/伪造 `X-CSRF-Token` → 403 `FORBIDDEN` 且审计零新增（含正确 CSRF 的正对照）；
BUILDER 访问 ADMIN 端点（`accounts`/`users`/`credentials`）→ 403（ADMIN 正对照非 403）；
跨租户资源不可见/不可写且与「不存在」同形（不泄露存在性）；伪造 `X-Tenant-Id` 不切换租户。
另含 RULE-auth-001 在本任务可机检的 Console 侧口径：Cookie 属性（httponly/samesite/secure）、
密码与失败锁定（argon2id 前缀、最短 12 字符、5 次失败上锁且计数清零、未知/禁用同一错误码）、
会话只存 sha256 摘要且 TTL 12h、登录与创建账号的审计和业务变更同事务。

不覆盖（归其他 owner，勿在此宣称）：三层授权与 Effective Capability 的应用服务口径（RULE-auth-001
verifier `tests/console_platform/test_user_side_relations.py -k s04` 承载）；授权变更对
Prompt/ToolRegistry/Catalog 的影响（S-10，归 TASK-011）；三处 internal 端点的服务身份门控
（E-07，归 TASK-007）；会话滑动续期与登出幂等（既有 `tests/console_auth/test_login.py` 承接，不在本 argv 内）；
ADMIN 门控的**前端隐藏**层（归前端 owner，本用例只断言可机检的后端 403 兜底层）。

收尾清理：本模块自建的账号/会话/审计与跨租户对照样本在 fixture 内 `finally` 删除——DFX 基座的
`cleanup()` 不覆盖 `console_account`/`console_session`/`config_audit_log`，不清会污染共享开发库。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import AgentDefinition, ModelDefinition
from sqlalchemy import text

from .environment import CROSS_TENANT, TENANT, DfxStack, count_rows, run_db

pytestmark = pytest.mark.integration

ADMIN_PASSWORD = "dfx-api-security-admin-pwd"
BUILDER_PASSWORD = "dfx-api-security-builder-pwd"
LOCKOUT_PASSWORD = "dfx-api-security-lockout-pwd"
# 11 字符：低于规范要求的 12 字符下限（校验层拒绝的真实边界）。
SHORT_PASSWORD = "dfx-short-1"
WRONG_PASSWORD = "dfx-wrong-password"
MAX_FAILED_ATTEMPTS = 5
EXPECTED_SESSION_TTL = timedelta(hours=12)

SESSION_COOKIE = "muad_session"
CSRF_COOKIE = "muad_csrf"
CSRF_HEADER = "X-CSRF-Token"
CROSS_AGENT_NAME = "DFX Cross Agent"


@dataclass(frozen=True)
class ApiSecurityContext:
    """本模块自建的真实盘面：两个租户的账号 + 跨租户资源 + 本租户模型。"""

    admin_username: str
    builder_username: str
    disabled_username: str
    tenant_model_id: uuid.UUID
    cross_admin_username: str
    cross_agent_id: uuid.UUID
    cross_agent_key: str


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _one(statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """真实库单行回读（mappings，便于按列名断言）。"""

    async def query(factory: Any) -> dict[str, Any] | None:
        async with factory() as session:
            row = (await session.execute(text(statement), params)).mappings().one_or_none()
        return dict(row) if row is not None else None

    return cast("dict[str, Any] | None", run_db(query))


def _seed() -> ApiSecurityContext:
    """自建两个租户的账号与跨租户对照资源（真实 PG 行）。"""

    async def seed(factory: Any) -> ApiSecurityContext:
        admin_username = _unique("dfx-admin")
        builder_username = _unique("dfx-builder")
        disabled_username = _unique("dfx-disabled")
        cross_admin_username = _unique("dfx-cross-admin")
        cross_agent_key = _unique("dfx-cross-agent")
        async with factory() as session:
            tenant_model_id = await session.scalar(
                text(
                    "SELECT id FROM control.model_definition"
                    " WHERE tenant_id = :t ORDER BY create_time LIMIT 1"
                ),
                {"t": TENANT},
            )
            assert tenant_model_id is not None, "DFX 基座未种下本租户模型（环境未就绪，不得 skip）"
            session.add_all(
                [
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=admin_username,
                        display_name="DFX Admin",
                        password_hash=hash_password(ADMIN_PASSWORD),
                        role=ROLE_ADMIN,
                    ),
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=builder_username,
                        display_name="DFX Builder",
                        password_hash=hash_password(BUILDER_PASSWORD),
                        role=ROLE_BUILDER,
                    ),
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=disabled_username,
                        display_name="DFX Disabled",
                        password_hash=hash_password(BUILDER_PASSWORD),
                        role=ROLE_BUILDER,
                        enabled=False,
                    ),
                    ConsoleAccount(
                        tenant_id=CROSS_TENANT,
                        username=cross_admin_username,
                        display_name="DFX Cross Admin",
                        password_hash=hash_password(ADMIN_PASSWORD),
                        role=ROLE_ADMIN,
                    ),
                ]
            )
            cross_model = ModelDefinition(
                tenant_id=CROSS_TENANT,
                key=_unique("dfx-cross-model"),
                name="DFX Cross Model",
                model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1",
                api_key="dfx-cross-model-key",
            )
            session.add(cross_model)
            await session.flush()
            cross_agent = AgentDefinition(
                tenant_id=CROSS_TENANT,
                key=cross_agent_key,
                name=CROSS_AGENT_NAME,
                instructions="你是跨租户对照样本。",
                model_id=cross_model.id,
            )
            session.add(cross_agent)
            await session.commit()
            return ApiSecurityContext(
                admin_username=admin_username,
                builder_username=builder_username,
                disabled_username=disabled_username,
                tenant_model_id=cast(uuid.UUID, tenant_model_id),
                cross_admin_username=cross_admin_username,
                cross_agent_id=cross_agent.id,
                cross_agent_key=cross_agent_key,
            )

    return cast(ApiSecurityContext, run_db(seed))


def _purge() -> None:
    """删除本模块自建行（两个租户的账号/会话/审计 + 跨租户控制面）。"""

    async def purge(factory: Any) -> None:
        async with factory() as session:
            for statement in (
                "DELETE FROM control.console_session WHERE account_id IN "
                "(SELECT id FROM control.console_account WHERE tenant_id IN (:t, :c))",
                "DELETE FROM control.console_account WHERE tenant_id IN (:t, :c)",
                "DELETE FROM control.config_audit_log WHERE tenant_id IN (:t, :c)",
                "DELETE FROM control.agent_definition WHERE tenant_id = :c",
                "DELETE FROM control.model_definition WHERE tenant_id = :c",
            ):
                await session.execute(text(statement), {"t": TENANT, "c": CROSS_TENANT})
            await session.commit()

    run_db(purge)


@pytest.fixture(scope="module")
def api_security(live_stack: DfxStack) -> Iterator[ApiSecurityContext]:
    context = _seed()
    try:
        yield context
    finally:
        _purge()


@pytest.fixture()
def http(live_stack: DfxStack) -> Iterator[httpx.Client]:
    """真实 HTTP 客户端：指向 DFX 基座里真实 uvicorn 子进程的 Console。"""
    with httpx.Client(base_url=live_stack.console_url, timeout=30) as client:
        yield client


def _headers(tenant_id: str = TENANT) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id}


def _csrf(client: httpx.Client) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}


def _login(
    client: httpx.Client, username: str, password: str, *, tenant_id: str = TENANT
) -> httpx.Response:
    """公开登录入口是 `X-Tenant-Id` 的显式 opt-in 场景（无会话可依、只能按头定位账号）。"""
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=_headers(tenant_id),
    )


def _login_admin(client: httpx.Client, context: ApiSecurityContext) -> None:
    client.cookies.clear()
    response = _login(client, context.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200, response.text


def _account_row(username: str, tenant_id: str = TENANT) -> dict[str, Any]:
    row = _one(
        "SELECT id, tenant_id, role, enabled, failed_attempts, locked_until, password_hash"
        " FROM control.console_account WHERE tenant_id = :t AND username = :u",
        {"t": tenant_id, "u": username},
    )
    assert row is not None, f"真实库中不存在账号 {username}"
    return row


def _agent_row(agent_id: uuid.UUID) -> dict[str, Any] | None:
    return _one(
        "SELECT id, tenant_id, name, revision, is_deleted"
        " FROM control.agent_definition WHERE id = :id",
        {"id": agent_id},
    )


def _create_account(
    client: httpx.Client, username: str, password: str, *, role: str = ROLE_BUILDER
) -> httpx.Response:
    return client.post(
        "/api/v1/accounts",
        json={
            "username": username,
            "display_name": "DFX API Security",
            "password": password,
            "role": role,
        },
        headers=_csrf(client),
    )


def _agent_payload(model_id: uuid.UUID, key: str) -> dict[str, Any]:
    return {
        "key": key,
        "name": "DFX API Security Agent",
        "instructions": "你是 API 安全验收用的 Agent。",
        "model_id": str(model_id),
    }


def _create_audit_count() -> int:
    return count_rows(
        "control.config_audit_log",
        TENANT,
        "action = :a AND resource_type = :r",
        {"a": "CREATE", "r": "CONSOLE_ACCOUNT"},
    )


def test_e08_csrf_missing_or_forged_is_forbidden_without_audit(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """非安全方法必须比对 `muad_csrf` Cookie 与 `X-CSRF-Token` 头；拒绝时不落任何变更。"""
    _login_admin(http, api_security)
    before = _create_audit_count()
    payload = {
        "username": _unique("dfx-csrf"),
        "display_name": "DFX CSRF",
        "password": BUILDER_PASSWORD,
        "role": ROLE_BUILDER,
    }

    missing = http.post("/api/v1/accounts", json=payload, headers=_headers())
    assert missing.status_code == 403, missing.text
    assert missing.json()["code"] == "FORBIDDEN"

    forged = http.post(
        "/api/v1/accounts", json=payload, headers={**_headers(), CSRF_HEADER: "forged-token"}
    )
    assert forged.status_code == 403, forged.text
    assert forged.json()["code"] == "FORBIDDEN"

    # 拒绝路径不得落业务变更，也不得写审计（两处 403 都不得改变计数）。
    assert _create_audit_count() == before
    assert _one(
        "SELECT id FROM control.console_account WHERE tenant_id = :t AND username = :u",
        {"t": TENANT, "u": payload["username"]},
    ) is None

    # 正对照：同一请求带正确 CSRF 头必须成功，证明 403 来自 CSRF 校验而非别的门禁。
    created = _create_account(http, payload["username"], BUILDER_PASSWORD)
    assert created.status_code == 200, created.text
    assert _create_audit_count() == before + 1


def test_e08_builder_is_denied_admin_endpoints_and_admin_is_allowed(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """ADMIN 门控的后端兜底层：`accounts`/`users`/`credentials` 非 ADMIN → 403 `FORBIDDEN`。"""
    http.cookies.clear()
    assert _login(http, api_security.builder_username, BUILDER_PASSWORD).status_code == 200
    platform_id = uuid.uuid4()
    admin_paths = (
        "/api/v1/accounts",
        "/api/v1/users",
        f"/api/v1/project-platforms/{platform_id}/user-credentials",
        f"/api/v1/project-platforms/{platform_id}/shared-credential",
    )
    for path in admin_paths:
        forbidden = http.get(path, headers=_headers())
        assert forbidden.status_code == 403, f"{path}: {forbidden.text}"
        assert forbidden.json()["code"] == "FORBIDDEN", path

    # 写面同样 403——带**正确** CSRF 头，证明拒绝来自角色门控而非 CSRF 校验。
    denied_write = _create_account(http, _unique("dfx-admin-only"), BUILDER_PASSWORD)
    assert denied_write.status_code == 403, denied_write.text
    assert denied_write.json()["code"] == "FORBIDDEN"

    # ADMIN 正对照：同一组端点必须放行（凭据路由走到业务层 → 404 而非 403）。
    _login_admin(http, api_security)
    assert http.get("/api/v1/accounts", headers=_headers()).status_code == 200
    assert http.get("/api/v1/users", headers=_headers()).status_code == 200
    credentials = http.get(
        f"/api/v1/project-platforms/{uuid.uuid4()}/user-credentials", headers=_headers()
    )
    assert credentials.status_code == 404, credentials.text


def test_e08_cross_tenant_resource_is_invisible_and_not_writable(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """跨租户不可见/不可写，且与「不存在」同形——不泄露存在性。"""
    _login_admin(http, api_security)

    listed = http.get("/api/v1/agents", headers=_headers())
    assert listed.status_code == 200, listed.text
    keys = [item["key"] for item in listed.json()["data"]["items"]]
    assert api_security.cross_agent_key not in keys, "他租户 Agent 不得出现在本租户列表"

    cross = http.get(f"/api/v1/agents/{api_security.cross_agent_id}", headers=_headers())
    unknown = http.get(f"/api/v1/agents/{uuid.uuid4()}", headers=_headers())
    assert cross.status_code == unknown.status_code == 404
    assert cross.json()["code"] == unknown.json()["code"], "跨租户读取必须与「不存在」同形"

    tampered = http.put(
        f"/api/v1/agents/{api_security.cross_agent_id}",
        json={"name": "tampered-by-cross-tenant", "expected_revision": 1},
        headers={**_headers(), **_csrf(http)},
    )
    assert tampered.status_code == 404, tampered.text
    assert tampered.json()["code"] == unknown.json()["code"]
    deleted = http.delete(
        f"/api/v1/agents/{api_security.cross_agent_id}", headers={**_headers(), **_csrf(http)}
    )
    assert deleted.status_code == 404, deleted.text

    # 真实库回读：他租户行未被改名、未被软删（「不落变更」不能只看响应码）。
    row = _agent_row(api_security.cross_agent_id)
    assert row is not None, "跨租户写入不得真的删行"
    assert row["tenant_id"] == CROSS_TENANT
    assert row["name"] == CROSS_AGENT_NAME
    assert row["is_deleted"] is False

    accounts = http.get("/api/v1/accounts", headers=_headers())
    assert accounts.status_code == 200
    usernames = [item["username"] for item in accounts.json()["data"]["items"]]
    assert api_security.cross_admin_username not in usernames


def test_e08_forged_tenant_header_does_not_switch_tenant(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """租户是主体属性：伪造 `X-Tenant-Id` 后仍只读到自己账号所属租户的数据。"""
    _login_admin(http, api_security)
    key = _unique("dfx-tenant-scope")
    created = http.post(
        "/api/v1/agents",
        json=_agent_payload(api_security.tenant_model_id, key),
        headers={**_headers(), **_csrf(http)},
    )
    assert created.status_code == 200, created.text

    forged = {"X-Tenant-Id": _unique("forged")}
    listed = http.get("/api/v1/agents", headers=forged)
    assert listed.status_code == 200, listed.text
    keys = [item["key"] for item in listed.json()["data"]["items"]]
    assert key in keys, "伪造 X-Tenant-Id 不得切走本账号租户（列表应仍是本账号租户）"
    assert api_security.cross_agent_key not in keys

    accounts = http.get("/api/v1/accounts", headers=forged)
    assert accounts.status_code == 200, accounts.text
    usernames = [item["username"] for item in accounts.json()["data"]["items"]]
    assert api_security.admin_username in usernames
    assert api_security.cross_admin_username not in usernames


def test_e08_session_and_csrf_cookie_attributes(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """`muad_session` httponly、CSRF Cookie 可读、两者 samesite=strict、secure 仅非 dev 打开。"""
    http.cookies.clear()
    response = _login(http, api_security.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200, response.text

    raw_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(
        line for line in raw_cookies if line.startswith(f"{SESSION_COOKIE}=")
    )
    csrf_cookie = next(line for line in raw_cookies if line.startswith(f"{CSRF_COOKIE}="))
    secure_expected = SharedSettings().env != "dev"

    assert "httponly" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower(), "CSRF Cookie 必须可被前端 JS 读取"
    for line in (session_cookie, csrf_cookie):
        attributes = [part.strip().lower() for part in line.split(";")[1:]]
        assert "samesite=strict" in attributes, line
        assert ("secure" in attributes) is secure_expected, line

    # CSRF 双提交：Cookie 值可读且与请求头一致时放行（BUILDER 可管理 Agent）。
    token = http.cookies.get(CSRF_COOKIE)
    assert token, "CSRF Cookie 未落进客户端 cookie jar"
    created = http.post(
        "/api/v1/agents",
        json=_agent_payload(api_security.tenant_model_id, _unique("dfx-csrf-positive")),
        headers={**_headers(), CSRF_HEADER: token},
    )
    assert created.status_code == 200, created.text


def test_e08_password_policy_and_failed_login_lockout(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """密码口径不被削弱：argon2id、最短 12 字符、5 次失败上锁且计数清零、未知/禁用同一错误码。"""
    _login_admin(http, api_security)

    rejected_username = _unique("dfx-short")
    short = _create_account(http, rejected_username, SHORT_PASSWORD)
    assert short.status_code == 422, short.text
    assert _one(
        "SELECT id FROM control.console_account WHERE tenant_id = :t AND username = :u",
        {"t": TENANT, "u": rejected_username},
    ) is None, "短密码请求必须整体被拒，不得落账号行"

    lockout_username = _unique("dfx-lockout")
    created = _create_account(http, lockout_username, LOCKOUT_PASSWORD)
    assert created.status_code == 200, created.text
    row = _account_row(lockout_username)
    assert row["password_hash"].startswith("$argon2id$"), "密码哈希必须是 argon2id"

    # 未知用户与禁用账号：状态码与错误码完全一致（不泄露账号是否存在）。
    http.cookies.clear()
    unknown = _login(http, _unique("dfx-unknown"), LOCKOUT_PASSWORD)
    disabled = _login(http, api_security.disabled_username, BUILDER_PASSWORD)
    assert unknown.status_code == disabled.status_code == 401
    assert unknown.json()["code"] == disabled.json()["code"] == "INVALID_CREDENTIALS"

    for attempt in range(1, MAX_FAILED_ATTEMPTS + 1):
        failed = _login(http, lockout_username, WRONG_PASSWORD)
        assert failed.status_code == 401, failed.text
        assert failed.json()["code"] == "INVALID_CREDENTIALS"
        if attempt < MAX_FAILED_ATTEMPTS:
            state = _account_row(lockout_username)
            assert state["failed_attempts"] == attempt
            assert state["locked_until"] is None

    # 第 5 次失败上锁，且按规范在锁定当时把计数清零（恢复后重新计 5 次）。
    locked_state = _account_row(lockout_username)
    assert locked_state["locked_until"] is not None, "连续 5 次失败必须上锁"
    assert locked_state["failed_attempts"] == 0, "锁定时必须清零计数"

    locked = _login(http, lockout_username, LOCKOUT_PASSWORD)
    assert locked.status_code == 423, locked.text
    assert locked.json()["code"] == "ACCOUNT_LOCKED"


def test_e08_session_row_is_hashed_and_login_audit_shares_transaction(
    live_stack: DfxStack, http: httpx.Client, api_security: ApiSecurityContext
) -> None:
    """会话只存 sha256 摘要（TTL 12h），登录审计与业务变更同事务且不含密码哈希。"""
    http.cookies.clear()
    before = count_rows(
        "control.config_audit_log", TENANT, "action = :a", {"a": "LOGIN"}
    )
    response = _login(http, api_security.admin_username, ADMIN_PASSWORD)
    assert response.status_code == 200, response.text
    token = http.cookies.get(SESSION_COOKIE)
    assert token, "登录未下发会话 Cookie"

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = _one(
        "SELECT account_id, token_hash, issued_at, expires_at, revoked_at"
        " FROM control.console_session WHERE token_hash = :h",
        {"h": digest},
    )
    assert row is not None, "会话行必须按 sha256 摘要可查"
    assert row["token_hash"] == digest
    assert row["revoked_at"] is None
    assert row["expires_at"] - row["issued_at"] == EXPECTED_SESSION_TTL
    assert _one(
        "SELECT id FROM control.console_session WHERE token_hash = :raw", {"raw": token}
    ) is None, "库里不得出现会话明文"

    account = _account_row(api_security.admin_username)
    assert str(row["account_id"]) == str(account["id"])
    # 登录成功写审计（与 last_login_at/会话插入同一事务）：恰好 +1 行，载荷不含密码哈希。
    assert count_rows("control.config_audit_log", TENANT, "action = :a", {"a": "LOGIN"}) == (
        before + 1
    )
    audit = _one(
        "SELECT actor_user_id, resource_type, resource_id, action, after_json, before_json"
        " FROM control.config_audit_log"
        " WHERE tenant_id = :t AND action = 'LOGIN' AND resource_id = :rid"
        " ORDER BY create_time DESC LIMIT 1",
        {"t": TENANT, "rid": account["id"]},
    )
    assert audit is not None
    assert str(audit["actor_user_id"]) == str(account["id"]), "actor 必须是账号本人"
    assert audit["resource_type"] == "CONSOLE_ACCOUNT"
    assert audit["after_json"] == {
        "username": api_security.admin_username,
        "role": ROLE_ADMIN,
    }
    assert audit["before_json"] is None
    assert "password" not in str(audit["after_json"]).lower()
