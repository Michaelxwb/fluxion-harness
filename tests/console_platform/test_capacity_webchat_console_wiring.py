"""B-297：[integration] Console 装配 CHAT 路由与探针资源预算（design §3.4/§3.7/§4.1；TASK-097）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务**：直连 `muad_console_platform.main.app`（真实路由表、真实 api-kit
  封套与错误目录、真实 ASGI 请求循环），探针预算断言跑**真实 lifespan**（装配根）；
- **真实 PostgreSQL 与事务**：`control.platform_user`/`chat_access_token`/`chat_session`/
  `platform_setting`/`console_account`/`console_session` 全部真实行与真实事务（每用例独立租户，
  只增删本租户行，无 DDL）；
- **真实 HTTP 头**：CHAT 与 Console 的 Cookie/CSRF/Origin 都走真实请求头；
- **真实构建产物目录**：SPA 断言用临时目录承载 `index.html`，装配走同一段 `create_app` 代码；
- 本文件不发起外部模型调用：探针的模型侧真实 HTTP 协议探针由 B-228（TASK-028）覆盖；这里证明的
  是**装配事实**——池/预算来自启动设置、请求路径消费注入实例、收尾关闭。

关键断言（B-297）：认证端点不被 Console 管理会话守卫吞并；admin 资源路由保持 ADMIN；探针独立
容量/HTTP 客户端按 typed 设置注入；坏启动配置显式失败；设置损坏不锁死已认证 CHAT；SPA 不把
API 错误回退 HTML。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings
from muad_console_platform import main
from muad_console_platform.api import model_resources
from muad_console_platform.api.chat_security import CHAT_SESSION_COOKIE
from muad_console_platform.api.deps import (
    get_probe_budget,
    get_probe_capacity_client,
    get_probe_http_client,
)
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.chat_token_service import ChatTokenService
from muad_console_platform.application.model_probe_service import (
    CAPACITY_REQUEST_TIMEOUT_SEC,
    PROBE_CONCURRENCY,
    PROBE_TIMEOUT_SEC,
    ProbeBudget,
    ProbeCapacityClient,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.http_clients import (
    control_pool_limits,
    model_pool_limits,
)
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import PlatformUser
from sqlalchemy import text
from starlette.requests import Request

from console_platform.conftest import TenantContext

pytestmark = pytest.mark.integration

SAME_ORIGIN = "http://test"
EXCHANGE_PATH = "/api/v1/webchat/auth/exchange"
ME_PATH = "/api/v1/webchat/auth/me"
ADMIN_RESOURCE_PATH = "/api/v1/model-credentials"
#: 管理会话守卫内的路径：CHAT 凭据不得让它们放行。
CONSOLE_GUARDED_PATHS = ("/api/v1/auth/me", "/api/v1/users", ADMIN_RESOURCE_PATH)
ADMIN_PASSWORD = "wiring-admin-password"
BUILDER_PASSWORD = "wiring-builder-password"
INDEX_HTML = "<!doctype html><html><body>console-wiring-spa</body></html>"
#: 本文件自建的租户行收尾（顺序满足外键）：会话 → Token → 用户 → 设置 → 管理账号。
PURGE_STATEMENTS = (
    "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
    "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :tenant_id)",
    "DELETE FROM control.console_account WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class ChatScope:
    """本用例租户里的平台用户：CHAT 身份只由 PG 行导出。"""

    tenant_id: str
    user_id: uuid.UUID


@pytest.fixture
async def chat_scope(tenant: TenantContext) -> AsyncIterator[ChatScope]:
    """真实平台用户 + 本文件自建行的收尾（唯一租户，无 DDL）。"""
    factory = get_session_factory()
    async with factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"u-{uuid.uuid4().hex[:8]}",
            display_name="Wiring User",
        )
        session.add(user)
        await session.commit()
        scope = ChatScope(tenant_id=tenant.tenant_id, user_id=user.id)
    try:
        yield scope
    finally:
        async with factory() as session:
            for statement in PURGE_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


# --- 真实服务/库内回读 -----------------------------------------------------------------


async def _issue_user_token(scope: ChatScope) -> str:
    """真实 `ChatTokenService` 首签：拿到可交换的用户 Token 原文。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            expected_revision=0,
            actor=AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.9"),
        )
        await session.commit()
        return issued.token.get_secret_value()


async def _add_console_account(tenant_id: str, role: str, password: str) -> ConsoleAccount:
    async with get_session_factory()() as session:
        account = ConsoleAccount(
            tenant_id=tenant_id,
            username=f"{role.lower()}-{uuid.uuid4()}",
            display_name=f"{role} Account",
            password_hash=hash_password(password),
            role=role,
        )
        session.add(account)
        await session.commit()
        return account


async def _login(client: AsyncClient, tenant_id: str, username: str, password: str) -> None:
    """真实登录：真实会话行 + 真实 CSRF Cookie（后续写请求要用）。"""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf
    client.headers[CSRF_HEADER] = csrf


async def _insert_corrupt_settings(tenant_id: str) -> None:
    """真实 SQL：植入现行 schema 不认识的设置版本（未知分组），不静默回落到默认值。"""
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_setting (tenant_id, revision, settings_json) "
                "VALUES (:tenant_id, 1, CAST(:document AS jsonb))"
            ),
            {"tenant_id": tenant_id, "document": json.dumps({"unknown_group": {"x": 1}})},
        )
        await session.commit()


async def _chat_session_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM control.chat_session WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            or 0
        )


def _request_bound_to(fastapi_app: FastAPI) -> Request:
    """请求路径上的探针依赖只读 `request.app.state`：用最小 scope 真实调用它们。"""
    return Request({"type": "http", "app": fastapi_app, "method": "GET", "path": "/", "headers": []})


def _effective_pool_limits(client: httpx.AsyncClient) -> tuple[int, int]:
    """httpx 不暴露池预算：从真实 transport 的 httpcore 池读**有效值**（装配断言的直接证据）。"""
    pool = client._transport._pool  # type: ignore[attr-defined]
    return pool._max_connections, pool._max_keepalive_connections  # type: ignore[attr-defined]


def _probe_endpoint_dependencies() -> set[str]:
    """探针路由声明的注入依赖（真实路由对象）：路由一旦不再声明，请求路径就必然自建。"""
    assert "/api/v1/model-credentials/test" in main.app.openapi()["paths"], "探针路由必须挂载"
    route = next(
        route
        for route in model_resources.router.routes
        if route.path == "/api/v1/model-credentials/test"
    )
    names: set[str] = set()

    def walk(dependant: object) -> None:
        for sub in dependant.dependencies:  # type: ignore[attr-defined]
            if sub.call is not None:
                names.add(sub.call.__name__)
            walk(sub)

    walk(route.dependant)
    return names


# --- B-297：公开 CHAT 路由 vs 管理守卫 ---------------------------------------------------


async def test_b297_chat_public_routes_stay_outside_the_console_login_guard(
    chat_scope: ChatScope,
) -> None:
    """[B-297] CHAT 公开认证面不需要 Console 会话，也不授予任何 Console 权限。"""
    user_token = await _issue_user_token(chat_scope)
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as client:
        assert client.cookies.get(SESSION_COOKIE) is None, "用例起点没有任何管理会话"

        anonymous = await client.get(ME_PATH)
        assert anonymous.status_code == 401, "路由真实挂载（不是 404），由 CHAT 层给出 401"
        assert anonymous.json()["code"] == "UNAUTHORIZED"

        exchanged = await client.post(EXCHANGE_PATH, json={"token": user_token})
        assert exchanged.status_code == 200, exchanged.text
        assert client.cookies.get(SESSION_COOKIE) is None, "CHAT 交换不得建立管理会话"
        assert client.cookies.get(CHAT_SESSION_COOKIE), "交换必须下发独立 CHAT Cookie"

        me = await client.get(ME_PATH)
        assert me.status_code == 200
        assert me.json()["data"]["user_id"] == str(chat_scope.user_id)

        for path in CONSOLE_GUARDED_PATHS:
            denied = await client.get(path)
            assert denied.status_code == 401, path
            assert denied.json()["code"] == "UNAUTHORIZED", path


async def test_b297_admin_resource_routes_keep_the_admin_role(chat_scope: ChatScope) -> None:
    """[B-297] 管理资源路由仍要求 ADMIN：无会话 401、BUILDER 403、ADMIN 可读。"""
    builder = await _add_console_account(chat_scope.tenant_id, ROLE_BUILDER, BUILDER_PASSWORD)
    admin = await _add_console_account(chat_scope.tenant_id, ROLE_ADMIN, ADMIN_PASSWORD)
    transport = ASGITransport(app=main.app)

    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as anonymous:
        response = await anonymous.get(ADMIN_RESOURCE_PATH)
        assert response.status_code == 401 and response.json()["code"] == "UNAUTHORIZED"

    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as builder_client:
        await _login(builder_client, chat_scope.tenant_id, builder.username, BUILDER_PASSWORD)
        denied = await builder_client.get(ADMIN_RESOURCE_PATH)
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"

    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as admin_client:
        await _login(admin_client, chat_scope.tenant_id, admin.username, ADMIN_PASSWORD)
        allowed = await admin_client.get(ADMIN_RESOURCE_PATH)
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["code"] == "0"


# --- B-297：探针资源预算与独立池（真实装配根） -------------------------------------------


async def test_b297_probe_budget_and_pools_are_injected_from_startup_settings(
    database_guard: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-297] 真实 lifespan：探针预算与两个独立池按启动设置装配，请求路径消费同一实例。"""
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", "6")
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", "3")
    # 默认 concurrency 是 256，模型池必须覆盖它；用例要钉住独立于本机 .env 的小值。
    monkeypatch.setenv("RUNTIME_EXECUTION_CONCURRENCY", "8")
    monkeypatch.setenv("MODEL_HTTP_MAX_CONNECTIONS", "9")
    monkeypatch.setenv("MODEL_HTTP_MAX_KEEPALIVE", "4")
    try:
        async with main.lifespan(main.app):
            budget = main.app.state.probe_budget
            capacity = main.app.state.probe_capacity_client
            probe_http = main.app.state.probe_http_client

            # 执行预算是契约常量（单次 5 秒、并发 4）：装配根不重抄、也不加运营旋钮
            assert budget == ProbeBudget(PROBE_TIMEOUT_SEC, PROBE_CONCURRENCY)
            assert isinstance(capacity, ProbeCapacityClient)
            assert isinstance(probe_http, httpx.AsyncClient)
            assert capacity is not probe_http, "容量端口与模型探测各自独立建池"

            # 池大小来自启动设置：改设置即改池（不是装配根里写死的默认值）
            assert _effective_pool_limits(capacity._client) == (6, 3)
            assert _effective_pool_limits(probe_http) == (9, 4)
            settings = SharedSettings()
            assert _effective_pool_limits(capacity._client) == (
                control_pool_limits(settings).max_connections,
                control_pool_limits(settings).max_keepalive_connections,
            )
            assert _effective_pool_limits(probe_http) == (
                model_pool_limits(settings).max_connections,
                model_pool_limits(settings).max_keepalive_connections,
            )

            # 超时预算：API-17 请求 5 秒；模型探测统一 5 秒（「单次 ≤5 秒」契约）
            assert capacity._client.timeout.read == CAPACITY_REQUEST_TIMEOUT_SEC
            assert probe_http.timeout.read == PROBE_TIMEOUT_SEC
            assert probe_http.timeout.connect == PROBE_TIMEOUT_SEC
            assert probe_http.timeout.write == PROBE_TIMEOUT_SEC

            # 请求路径消费装配根注入的**同一实例**（不是各自再造一个）
            request = _request_bound_to(main.app)
            assert get_probe_capacity_client(request) is capacity
            assert get_probe_http_client(request) is probe_http
            assert get_probe_budget(request) == budget
            assert {
                "get_probe_capacity_client",
                "get_probe_http_client",
                "get_probe_budget",
            } <= _probe_endpoint_dependencies()

            assert not capacity._client.is_closed and not probe_http.is_closed
        assert capacity._client.is_closed, "装配根建的容量池随进程关闭"
        assert probe_http.is_closed, "装配根建的模型池随进程关闭"
    finally:
        # 关闭后的实例不留给后续用例：装配根注入的生命周期属于本用例
        for name in ("probe_capacity_client", "probe_http_client", "probe_budget"):
            setattr(main.app.state, name, None)


@pytest.mark.parametrize(
    ("variable", "budget_name"),
    [
        ("MODEL_HTTP_READ_TIMEOUT_SEC", "probe single-shot"),
        ("CONTROL_HTTP_READ_TIMEOUT_SEC", "probe capacity request"),
    ],
)
async def test_b297_probe_budget_conflicts_fail_startup_explicitly(
    monkeypatch: pytest.MonkeyPatch, variable: str, budget_name: str
) -> None:
    """[B-297] 部署池读预算低于探针契约预算 ⇒ 启动显式失败，不静默取较小值。"""
    monkeypatch.setenv(variable, "3")
    with pytest.raises(ValueError, match=budget_name):
        async with main.lifespan(main.app):
            pass


def test_b297_probe_budget_type_rejects_non_positive_values() -> None:
    """[B-297] 预算类型自身拒绝零/负值：不给执行器一个"必然立即超时"的装配值。"""
    with pytest.raises(ValueError):
        ProbeBudget(timeout_sec=0)
    with pytest.raises(ValueError):
        ProbeBudget(concurrency=0)


# --- B-297：设置损坏不锁死已认证 CHAT ---------------------------------------------------


async def test_b297_corrupt_platform_settings_do_not_lock_out_authenticated_chat(
    chat_scope: ChatScope,
) -> None:
    """[B-297] 设置源损坏：已认证 CHAT 读取照常，新交换显式 503（不静默回落默认）。"""
    user_token = await _issue_user_token(chat_scope)
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url=SAME_ORIGIN) as client:
        exchanged = await client.post(EXCHANGE_PATH, json={"token": user_token})
        assert exchanged.status_code == 200, exchanged.text
        assert await _chat_session_count(chat_scope.tenant_id) == 1

        await _insert_corrupt_settings(chat_scope.tenant_id)

        me = await client.get(ME_PATH)
        assert me.status_code == 200, "已认证 CHAT 读取不依赖设置源"
        assert me.json()["data"]["user_id"] == str(chat_scope.user_id)

        again = await client.post(EXCHANGE_PATH, json={"token": user_token})
        assert again.status_code == 503, again.text
        assert again.json()["code"] == "PLATFORM_SETTINGS_UNAVAILABLE"
        assert await _chat_session_count(chat_scope.tenant_id) == 1, "失败交换不得留下半写出会话"


# --- B-297：SPA 兜底不吞 API 错误 -------------------------------------------------------


async def test_b297_spa_fallback_never_turns_api_errors_into_html(tmp_path: Path) -> None:
    """[B-297] 装配了构建产物时：页面深链回落 index.html，API 错误保持 JSON 封套。"""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    wired = main.create_app(static_dir=static_dir)
    async with AsyncClient(transport=ASGITransport(app=wired), base_url=SAME_ORIGIN) as client:
        page = await client.get("/chat/entry-1")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "console-wiring-spa" in page.text

        for path in (*CONSOLE_GUARDED_PATHS, "/api/v1/definitely-missing"):
            response = await client.get(path)
            assert response.headers["content-type"].startswith("application/json"), path

        missing = await client.get("/api/v1/definitely-missing")
        assert missing.status_code == 404
        assert missing.json()["code"] == "COMMON_NOT_FOUND"
