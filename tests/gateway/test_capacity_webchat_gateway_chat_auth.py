"""B-244 Gateway CHAT 主体及长流复核（design §3.4 API-12/API-19、§3.7；TASK-044）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与依赖**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套与
  错误目录、真实 `api/deps.py` 依赖图与真实 `StreamingResponse`）。生产 webchat 资源路由由
  TASK-052 挂载；本文件在**同一个真实应用**上挂两条 `_probe` 路由，让"读写"与"SSE 长流"都走
  真实 FastAPI 依赖链，装配根状态（Console 客户端 / 部署租户 / 复核周期）按生产同形注入。
- **真实 Console HTTP（Owner 接口）**：`ConsoleClient` 打真实本地 uvicorn 服务（真实 socket），
  该服务按 API-12 `POST /internal/channel/chat-principal` 的封套契约应答；网关不持库，主体、
  授权集合与到期时刻只从它的响应导出。
- **真实 HTTP/SSE 协议**：请求走真实 ASGI/HTTP 封套，长流是真实 `text/event-stream` 响应。

断言要点：

- 只经 Console 核验：缺 CHAT Cookie / 缺上下文头在**触达 Console 之前**就被拒；服务身份、部署
  租户与 `X-Chat-Context` 原样转发；主体只从响应导出，CHAT 凭据不当 Console 凭据转发；
- 拒绝跨部署租户：Console 若返回他租户主体（或漏判上下文），网关自己再拒一次（403/409）；
- 长流复核：空闲（无事件）也按 `chat_auth_recheck_sec` 周期复核，撤销在复核边界内生效，到
  `expires_at` 即停且**显式抛错**不静默收流；事件洪峰不产生按 token 的控制面查询；
- CHAT 身份不得授予 Console/管理权限，会话原文不进响应/日志/repr。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import StubConsole
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from httpx import ASGITransport, AsyncClient
from muad_api import ApiResponse, AppError, ok
from muad_im_gateway.api.deps import ChatIdentityDep, ChatStreamGuardDep
from muad_im_gateway.application.chat_principal import (
    CHAT_SESSION_COOKIE,
    ChatIdentity,
    ChatPrincipalGate,
    ChatSessionCredentials,
    ChatStreamGuard,
)
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.main import app
from pydantic import SecretStr

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-1"
WRITE_URL = "/api/v1/webchat/_probe/write"
STREAM_URL = "/api/v1/webchat/_probe/stream"
#: 统一 principal 恰好这四个字段（design §3.4）；多一个都是权限面。
PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}
#: 管理角色的 Console 语义字段：CHAT 主体不得携带任何一个。
CONSOLE_ROLE_FIELDS = {"role", "roles", "permissions", "username", "account_id", "user_code"}
AGENT_FIELDS = {"agent_id", "key", "name"}
#: CHAT 会话原文 canary：只允许出现在发往 Console 的请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"


# --- 真实 Gateway 应用上的探针路由（TASK-052 的生产路由同形） ---------------------------


@dataclass
class _ProbeScript:
    """SSE 探针脚本：先产出给定事件，之后可选空闲等待（验证"空闲也复核"）。"""

    events: tuple[str, ...] = ()
    idle_after: bool = False
    terminations: list[str] = field(default_factory=list)


_probe_router = APIRouter(prefix="/api/v1/webchat/_probe", tags=["webchat-probe"])


@_probe_router.post("/write")
async def _probe_write(request: Request, chat: ChatIdentityDep) -> ApiResponse[Any]:
    """读写面探针：TASK-052 的资源路由以同一条 `ChatIdentityDep` 收口主体。"""
    return ok(
        request.app.state.message_catalog,
        {
            "principal": chat.principal.model_dump(mode="json"),
            "agents": [agent.model_dump(mode="json") for agent in chat.agents],
            "expires_at": chat.expires_at.isoformat(),
        },
    )


@_probe_router.get("/stream")
async def _probe_stream(request: Request, guard: ChatStreamGuardDep) -> StreamingResponse:
    """长流探针：真实 `StreamingResponse` + 真实复核器（TASK-052/061 的 SSE 路由同形）。"""
    script: _ProbeScript = request.app.state.probe_script
    return StreamingResponse(_guarded_events(guard, script), media_type="text/event-stream")


async def _guarded_events(guard: ChatStreamGuard, script: _ProbeScript) -> AsyncIterator[str]:
    try:
        async for event in guard.rechecked(_scripted_events(script)):
            yield event
    except AppError as exc:
        # 生产路由把安全错误码投影成公开事件（TASK-051/061）；探针只登记终止原因。
        script.terminations.append(exc.code)


async def _scripted_events(script: _ProbeScript) -> AsyncIterator[str]:
    for event in script.events:
        yield event
    if script.idle_after:
        await asyncio.Event().wait()  # 永不就绪：空闲流只能由复核定时器终止


app.include_router(_probe_router)


# --- Console Owner 接口的真实 HTTP 桩（真实 socket + 真实封套） -------------------------


@dataclass(frozen=True)
class _StubCall:
    headers: dict[str, str]
    body: dict[str, Any]
    at: float


class _ConsolePrincipalStub:
    """API-12 的真实 HTTP 桩：记录每次调用并按脚本应答（主体只从这里导出）。"""

    def __init__(self, stub: StubConsole) -> None:
        self.session_id = uuid.uuid4()
        self.platform_user_id = uuid.uuid4()
        self.tenant_id = GATEWAY_TENANT
        self.expires_at = datetime.now(UTC) + timedelta(hours=1)
        self.agents: list[dict[str, str]] = []
        self.error_code: str | None = None
        self.error_status = 401
        self.error_after_calls = 0
        self.calls: list[_StubCall] = []
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._handle

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def fail(self, code: str, *, status: int = 401, after_calls: int = 0) -> None:
        """前 `after_calls` 次成功、之后按 `code` 拒绝（模拟复核期间被撤销/过期）。"""
        self.error_code = code
        self.error_status = status
        self.error_after_calls = after_calls

    async def _handle(self, request: Request) -> Response:
        body = json.loads(await request.body() or b"{}")
        self.calls.append(_StubCall(dict(request.headers), body, time.monotonic()))
        if self.error_code is not None and len(self.calls) > self.error_after_calls:
            return self._envelope(self.error_status, self.error_code)
        return self._envelope(200, "0", self._principal_data())

    def _principal_data(self) -> dict[str, Any]:
        return {
            "principal": {
                "tenant_id": self.tenant_id,
                "platform_user_id": str(self.platform_user_id),
                "session_id": str(self.session_id),
                "auth_method": "USER_TOKEN",
            },
            "expires_at": self.expires_at.isoformat(),
            "agents": list(self.agents),
        }

    def _envelope(self, status: int, code: str, data: Any = None) -> Response:
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "msg": "stub",
                "data": data,
                "trace_id": "trace-stub",
                "request_id": "request-stub",
                "timestamp": "2026-10-18T00:00:00Z",
            },
        )


# --- 夹具：真实 HTTP 桩 + 真实 Gateway 应用（按生产装配根状态注入） ----------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def console_stub(stub_console: StubConsole) -> _ConsolePrincipalStub:
    return _ConsolePrincipalStub(stub_console)


@asynccontextmanager
async def _gateway_client(
    stub: StubConsole,
    *,
    recheck_sec: float = 30.0,
    tenant: str = GATEWAY_TENANT,
    script: _ProbeScript | None = None,
    console_url: str | None = None,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 `ConsoleClient`；只注入装配根会发布的那几个状态值。"""
    previous: dict[str, Any] = {
        name: getattr(app.state, name, None)
        for name in ("console_client", "tenant_id", "chat_auth_recheck_sec", "probe_script")
    }
    console = ConsoleClient(console_url or stub.url)
    app.state.console_client = console
    app.state.tenant_id = tenant
    app.state.chat_auth_recheck_sec = recheck_sec
    app.state.probe_script = script or _ProbeScript()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://gateway.test",
            cookies=_chat_cookies() if cookies is None else cookies,
        ) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)
        await console.aclose()


@asynccontextmanager
async def _console_client(stub: StubConsole) -> AsyncIterator[ConsoleClient]:
    client = ConsoleClient(stub.url)
    try:
        yield client
    finally:
        await client.aclose()


def _chat_headers(context: uuid.UUID | str) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


def _chat_cookies() -> dict[str, str]:
    return {CHAT_SESSION_COOKIE: SESSION_TOKEN}


def _credentials(console_stub: _ConsolePrincipalStub) -> ChatSessionCredentials:
    return ChatSessionCredentials(
        session_token=SecretStr(SESSION_TOKEN), chat_context=console_stub.session_id
    )


async def _idle_events() -> AsyncIterator[str]:
    """永不产出的事件源：空闲流只能由复核定时器终止。"""
    await asyncio.Event().wait()
    yield ""  # pragma: no cover - 永不产出


# --- 请求级主体门控 -------------------------------------------------------------------


async def test_b244_missing_credentials_are_rejected_before_touching_console(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """缺 CHAT Cookie / 缺上下文 / 只有 Console 凭据：都在触达 Console 之前被拒。"""
    rejected: list[Any] = []
    async with _gateway_client(stub_console, cookies={}) as client:
        rejected.append(await client.post(WRITE_URL, headers=_chat_headers(console_stub.session_id)))
        # SSE 长流同样先过主体门控：缺凭据连流都建不起来
        rejected.append(await client.get(STREAM_URL, headers=_chat_headers(console_stub.session_id)))
        # Authorization/Bearer 不是 CHAT 凭据
        rejected.append(
            await client.post(
                WRITE_URL,
                headers=_chat_headers(console_stub.session_id)
                | {"Authorization": f"Bearer {SESSION_TOKEN}"},
            )
        )

    async with _gateway_client(stub_console) as client:
        rejected.append(await client.post(WRITE_URL))
        rejected.append(await client.post(WRITE_URL, headers=_chat_headers("not-a-uuid")))

    async with _gateway_client(stub_console, cookies={"muad_session": SESSION_TOKEN}) as client:
        # Console 管理会话 Cookie 也不是 CHAT 凭据（CHAT 面不能反向复用管理面凭据）
        rejected.append(await client.post(WRITE_URL, headers=_chat_headers(console_stub.session_id)))

    assert console_stub.call_count == 0, "缺凭据的请求不得触达 Console"
    assert [response.json()["code"] for response in rejected] == [
        "UNAUTHORIZED",
        "UNAUTHORIZED",
        "UNAUTHORIZED",
        "COMMON_VALIDATION_ERROR",
        "COMMON_VALIDATION_ERROR",
        "UNAUTHORIZED",
    ]
    assert [response.status_code for response in rejected] == [401, 401, 401, 422, 422, 401]
    for response in rejected:
        assert SESSION_TOKEN not in response.text


async def test_b244_gateway_only_verifies_through_console_and_projects_the_principal(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """只经 Console 核验：租户/服务身份/上下文原样转发，主体只从响应导出，无凭据回显。"""
    console_stub.agents = [{"agent_id": str(uuid.uuid4()), "key": "policy-check", "name": "策略检查"}]
    async with _gateway_client(stub_console) as client:
        response = await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data["principal"]) == PRINCIPAL_FIELDS
    assert data["principal"] == {
        "tenant_id": GATEWAY_TENANT,
        "platform_user_id": str(console_stub.platform_user_id),
        "session_id": str(console_stub.session_id),
        "auth_method": "USER_TOKEN",
    }
    assert [agent["key"] for agent in data["agents"]] == ["policy-check"]
    assert set(data["agents"][0]) == AGENT_FIELDS

    assert console_stub.call_count == 1, "一次请求只核验一次"
    call = console_stub.calls[0]
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert call.headers["x-chat-context"] == str(console_stub.session_id)
    assert call.body == {"session_token": SESSION_TOKEN}
    # CHAT 凭据不是 Console 凭据：不转发管理面 Authorization/Cookie
    assert "authorization" not in call.headers
    assert "cookie" not in call.headers

    assert SESSION_TOKEN not in response.text
    assert CONSOLE_ROLE_FIELDS.isdisjoint(set(data["principal"]))
    assert CONSOLE_ROLE_FIELDS.isdisjoint(set(data["agents"][0]))


async def test_b244_foreign_deployment_tenant_principal_is_rejected_by_the_gateway(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """Console 若返回他租户主体（或将来漏判），网关自己再拒一次：单租户部署不接受跨租户身份。"""
    console_stub.tenant_id = "another-tenant"
    async with _gateway_client(stub_console) as client:
        response = await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
    assert SESSION_TOKEN not in response.text
    assert "principal" not in response.text


async def test_b244_context_mismatch_is_rejected_even_if_console_accepts(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """`principal.session_id` 与 `X-Chat-Context` 不一致：网关自己拒（409），防切用户误用身份。"""
    other_context = uuid.uuid4()
    async with _gateway_client(stub_console) as client:
        response = await client.post(
            WRITE_URL, headers=_chat_headers(other_context)
        )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "AUTH_CONTEXT_CHANGED"
    assert SESSION_TOKEN not in response.text
    assert "principal" not in response.text


async def test_b244_console_rejection_and_outage_are_explicit_and_credential_free(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """撤销/过期/Console 不可用：错误码显式上抛，响应不含凭据也不含主体。"""
    responses: list[tuple[str, Any]] = []
    async with _gateway_client(stub_console) as client:
        console_stub.fail("CHAT_SESSION_REVOKED")
        responses.append(("CHAT_SESSION_REVOKED", await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )))
        console_stub.fail("CHAT_SESSION_EXPIRED")
        responses.append(("CHAT_SESSION_EXPIRED", await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )))
        console_stub.fail("COMMON_INTERNAL_ERROR", status=500)
        responses.append(("COMMON_INTERNAL_ERROR", await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )))
    async with _gateway_client(stub_console, console_url="http://127.0.0.1:1") as client:
        responses.append(("COMMON_INTERNAL_ERROR", await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )))

    for code, response in responses:
        assert response.json()["code"] == code, response.text
        assert SESSION_TOKEN not in response.text
        assert "principal" not in response.text
    assert [response.status_code for _, response in responses] == [401, 401, 500, 500]


async def test_b244_chat_identity_carries_no_console_permissions(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """CHAT 身份是统一 principal + 授权入口集合：没有角色/权限/Console 账号字段。"""
    async with _console_client(stub_console) as console:
        gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
        identity = await gate.resolve(_credentials(console_stub))

    assert isinstance(identity, ChatIdentity)
    assert set(identity.principal.model_dump()) == PRINCIPAL_FIELDS
    assert {f.name for f in dataclasses.fields(identity)} == {"principal", "expires_at", "agents"}
    assert CONSOLE_ROLE_FIELDS.isdisjoint(set(identity.principal.model_dump()))


def test_b244_chat_cookie_name_is_the_console_wire_contract() -> None:
    """CHAT Cookie 名是 Console 签发、网关读取的同一枚（同源浏览器只带一份）。"""
    from muad_console_platform.api.chat_security import CHAT_SESSION_COOKIE as CONSOLE_COOKIE

    assert CHAT_SESSION_COOKIE == CONSOLE_COOKIE


# --- 长流（SSE）复核 ------------------------------------------------------------------


async def test_b244_idle_stream_rechecks_on_the_timer_and_stops_at_expiry(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """空闲流（没有事件）也按复核周期查控制面；到 `expires_at` 即停。"""
    console_stub.expires_at = datetime.now(UTC) + timedelta(seconds=0.35)
    script = _ProbeScript(events=("data: first\n\n",), idle_after=True)
    started = time.monotonic()
    async with _gateway_client(stub_console, recheck_sec=0.05, script=script) as client:
        response = await client.get(
            STREAM_URL, headers=_chat_headers(console_stub.session_id)
        )
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "data: first\n\n"
    assert script.terminations == ["CHAT_SESSION_EXPIRED"]
    assert console_stub.call_count >= 3, "初始核验 1 次 + 空闲窗口内至少 2 次定时复核"
    assert console_stub.calls[-1].at - console_stub.calls[0].at <= 0.5, "复核节奏不得越过到期时刻"
    assert 0.25 <= elapsed < 1.0, "流应停在该到期时刻附近，而不是首次复核就停"
    assert SESSION_TOKEN not in response.text


async def test_b244_revoked_session_stops_an_idle_stream_within_the_recheck_bound(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """撤销在复核边界内对长流生效：空闲流下一次复核就终止（不等到会话到期）。"""
    console_stub.fail("CHAT_SESSION_REVOKED", after_calls=1)
    script = _ProbeScript(events=("data: first\n\n",), idle_after=True)
    started = time.monotonic()
    async with _gateway_client(stub_console, recheck_sec=0.05, script=script) as client:
        response = await client.get(
            STREAM_URL, headers=_chat_headers(console_stub.session_id)
        )
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert response.text == "data: first\n\n"
    assert script.terminations == ["CHAT_SESSION_REVOKED"]
    assert console_stub.call_count == 2, "初始核验 1 次 + 首次定时复核（撤销在此生效）"
    assert elapsed < 0.5
    assert SESSION_TOKEN not in response.text


async def test_b244_event_burst_does_not_query_the_control_plane_per_token(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """事件洪峰只走事件转发：控制面查询不随事件/token 增长。"""
    events = tuple(f"data: {index}\n\n" for index in range(40))
    script = _ProbeScript(events=events)
    async with _gateway_client(stub_console, recheck_sec=30.0, script=script) as client:
        response = await client.get(
            STREAM_URL, headers=_chat_headers(console_stub.session_id)
        )

    assert response.status_code == 200, response.text
    assert response.text == "".join(events)
    assert console_stub.call_count == 1, "事件洪峰不得产生按 token 的控制面查询"
    assert script.terminations == []


async def test_b244_stream_stops_at_expiry_by_raising_not_by_silence(
    stub_console: StubConsole, console_stub: _ConsolePrincipalStub
) -> None:
    """终止语义是**显式抛错**（不静默收流）：调用方能区分"流结束"与"身份失效"。"""
    console_stub.expires_at = datetime.now(UTC) + timedelta(seconds=0.08)
    async with _console_client(stub_console) as console:
        gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
        credentials = _credentials(console_stub)
        identity = await gate.resolve(credentials)
        guard = ChatStreamGuard(gate, credentials, identity, recheck_sec=0.03)
        with pytest.raises(AppError) as excinfo:
            async for _ in guard.rechecked(_idle_events()):
                pass

    assert excinfo.value.code == "CHAT_SESSION_EXPIRED"
    assert console_stub.call_count >= 2, "到期前仍要按周期复核"


# --- 凭据卫生 -------------------------------------------------------------------------


async def test_b244_session_token_never_reaches_logs_or_responses(
    stub_console: StubConsole,
    console_stub: _ConsolePrincipalStub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """会话原文只进发往 Console 的请求体：响应、日志与 repr 里都没有它。"""
    caplog.set_level(logging.DEBUG)
    async with _gateway_client(stub_console) as client:
        accepted = await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )
        console_stub.fail("CHAT_SESSION_EXPIRED")
        rejected = await client.post(
            WRITE_URL, headers=_chat_headers(console_stub.session_id)
        )

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 401, rejected.text
    assert SESSION_TOKEN not in accepted.text
    assert SESSION_TOKEN not in rejected.text
    assert all(SESSION_TOKEN not in record.getMessage() for record in caplog.records)
    credentials = _credentials(console_stub)
    assert SESSION_TOKEN not in repr(credentials)
    assert credentials.session_token.get_secret_value() == SESSION_TOKEN
