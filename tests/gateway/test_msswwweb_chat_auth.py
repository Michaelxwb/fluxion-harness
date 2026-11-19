"""MSSWWEB 断言面 CHAT 认证（设计 API-16、RULE-01/02；TASK-013 前置补齐）。

真实边界：

- **真实依赖函数**：直接调用 `api/deps.py` 的 `get_chat_credentials` / `get_chat_identity` /
  `get_chat_stream_guard`，以及 `ChatPrincipalGate` / `ChatStreamGuard` 本体，不 mock 业务判断。
- **真实 `Request`**：用 Starlette `Request` 携带真实 headers/cookies 与 `app.state` 挂载表，
  走与生产一致的 surface 解析。
- **Console 只是端口替身**：断言面核验在 Console，网关本地无需真实验签——这里只验证
  「转发什么、怎么判、判完给什么」。

断言要点：

- `EXTERNAL_ASSERTION` 面只认 `soc-token`：Cookie / Authorization 一律不构成凭据（RULE-01）；
- 断言原文只以 `SecretStr` 转发，`platform_key` / `channel` / 部署租户随行，无上下文头；
- `allowed=false` 按原因转 401/403（UNBOUND / NO_ENTRY_GRANT / NO_AGENT_GRANT / USER_DISABLED）；
- `allowed=true` 但主体跨部署租户 → 403；缺 `expires_at`（契约防御）→ `EXTERNAL_ASSERTION_INVALID`；
- 平台未配置（surface 没有 `platform_key`）→ `EXTERNAL_PLATFORM_UNAVAILABLE`，不触达 Console；
- `USER_TOKEN` 面行为不变：缺 Cookie 401、上下文头非法 422；
- 长流复核对断言面**重放断言原文**（不是会话 token），并在 `expires_at` 到点即停。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import FakeConsoleClient
from fastapi import FastAPI
from muad_api import AppError
from muad_contracts.webchat import (
    ChatAuthorizedAgent,
    ChatPrincipal,
    ExternalPrincipalResult,
)
from muad_im_gateway.api.deps import (
    get_chat_credentials,
    get_chat_identity,
    get_chat_stream_guard,
)
from muad_im_gateway.application.chat_principal import (
    CHAT_SESSION_COOKIE,
    ChatIdentity,
    ChatPrincipalGate,
    ChatSessionCredentials,
    ChatStreamGuard,
)
from muad_im_gateway.application.webchat_core import ChatSurface
from pydantic import SecretStr
from starlette.requests import Request

GATEWAY_TENANT = "tenant-1"
PLATFORM_KEY = "mssw"
ASSERTION = f"soc-token-{uuid.uuid4().hex}"


_CATALOG: object | None = None


def _catalog():
    """真实消息目录（`config/api-messages.yaml`），进程内复用一次。"""
    global _CATALOG
    if _CATALOG is None:
        from pathlib import Path

        from muad_api.catalog import MessageCatalog

        root = Path(__file__).resolve().parents[2]
        _CATALOG = MessageCatalog(root / "config" / "api-messages.yaml")
    return _CATALOG


def _request(
    path: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    surfaces: dict[str, ChatSurface] | None = None,
    state: dict[str, Any] | None = None,
) -> Request:
    """真实 `Request`：真实 headers/cookies + 真实 `app.state`（surface 挂载表与装配状态）。"""
    app = FastAPI()
    app.state.webchat_surfaces = surfaces or {
        "/api/v1/webchat": ChatSurface(channel="WEBCHAT", auth_kind="USER_TOKEN"),
        "/api/v1/msswwweb": ChatSurface(
            channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION", platform_key=PLATFORM_KEY
        ),
    }
    # 路由层的 `ok()` 要读 `app.state.message_catalog`（生产由装配注入）——这里用真实目录，
    # 避免用桩掩盖「错误码在 catalog 里不存在」这类问题。
    app.state.message_catalog = _catalog()
    for name, value in (state or {}).items():
        setattr(app.state, name, value)
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()
    ]
    if cookies:
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        raw_headers.append((b"cookie", cookie_header.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": raw_headers,
        "app": app,
        "query_string": b"",
    }
    return Request(scope)


def _credentials_for(request: Request) -> Any:
    """按真实 surface 解析结果调用凭据依赖（与生产依赖注入同一条路径）。"""
    from muad_im_gateway.application.webchat_surfaces import get_webchat_surface

    return get_chat_credentials(request, get_webchat_surface(request))


def _external_identity(
    *,
    tenant_id: str = GATEWAY_TENANT,
    expires_in: timedelta = timedelta(hours=1),
    agents: tuple[ChatAuthorizedAgent, ...] = (),
) -> ChatIdentity:
    return ChatIdentity(
        principal=ChatPrincipal(
            tenant_id=tenant_id,
            platform_user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            auth_method="EXTERNAL_ASSERTION",
        ),
        expires_at=datetime.now(UTC) + expires_in,
        agents=agents,
    )


def _allowed_result(
    identity: ChatIdentity | None = None,
) -> ExternalPrincipalResult:
    identity = identity or _external_identity()
    return ExternalPrincipalResult(
        allowed=True,
        principal=identity.principal,
        agents=list(identity.agents),
        expires_at=identity.expires_at,
    )


# --- 凭据分流：EXTERNAL_ASSERTION 只认 soc-token ---------------------------------------


async def test_assertion_surface_reads_soc_token_header_only() -> None:
    credentials = await _credentials_for(
        _request("/api/v1/msswwweb/entries", headers={"soc-token": ASSERTION})
    )
    assert credentials.assertion is not None
    assert credentials.assertion.get_secret_value() == ASSERTION
    assert credentials.session_token is None
    assert credentials.chat_context is None


async def test_assertion_surface_rejects_missing_soc_token() -> None:
    with pytest.raises(AppError) as excinfo:
        await _credentials_for(_request("/api/v1/msswwweb/entries"))
    assert excinfo.value.code == "UNAUTHORIZED"


async def test_assertion_surface_rejects_blank_soc_token() -> None:
    with pytest.raises(AppError) as excinfo:
        await _credentials_for(_request("/api/v1/msswwweb/entries", headers={"soc-token": "  "}))
    assert excinfo.value.code == "UNAUTHORIZED"


async def test_assertion_surface_ignores_session_cookie_and_authorization() -> None:
    """RULE-01：断言面不接受 WebChat Cookie 或 Bearer，缺 soc-token 就是 401。"""
    with pytest.raises(AppError) as excinfo:
        await _credentials_for(
            _request(
                "/api/v1/msswwweb/entries",
                headers={"Authorization": f"Bearer {ASSERTION}"},
                cookies={CHAT_SESSION_COOKIE: ASSERTION},
            )
        )
    assert excinfo.value.code == "UNAUTHORIZED"


async def test_user_token_surface_still_requires_cookie_and_context() -> None:
    """USER_TOKEN 面行为不变：Cookie + `X-Chat-Context`，缺上下文仍是 422 级坏请求。"""
    context = uuid.uuid4()
    credentials = await _credentials_for(
        _request(
            "/api/v1/webchat/entries",
            headers={"X-Chat-Context": str(context)},
            cookies={CHAT_SESSION_COOKIE: ASSERTION},
        )
    )
    assert credentials.session_token is not None
    assert credentials.session_token.get_secret_value() == ASSERTION
    assert credentials.chat_context == context
    assert credentials.assertion is None

    with pytest.raises(AppError) as missing_context:
        await _credentials_for(_request("/api/v1/webchat/entries", cookies={CHAT_SESSION_COOKIE: ASSERTION}))
    assert missing_context.value.code == "COMMON_VALIDATION_ERROR"

    with pytest.raises(AppError) as malformed:
        await _credentials_for(
            _request(
                "/api/v1/webchat/entries",
                headers={"X-Chat-Context": "not-a-uuid"},
                cookies={CHAT_SESSION_COOKIE: ASSERTION},
            )
        )
    assert malformed.value.code == "COMMON_VALIDATION_ERROR"


# --- 主体门控：经 Console 校验并导出 ----------------------------------------------------


async def test_gate_forwards_assertion_platform_and_channel_without_context_header() -> None:
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM_KEY,
        channel="MSSWWEB",
    )

    assert isinstance(identity, ChatIdentity)
    assert identity.principal.auth_method == "EXTERNAL_ASSERTION"
    assert len(console.external_principal_calls) == 1
    forwarded_assertion, forwarded_tenant, forwarded_platform, forwarded_channel = (
        console.external_principal_calls[0]
    )
    assert isinstance(forwarded_assertion, SecretStr)
    assert forwarded_assertion.get_secret_value() == ASSERTION
    assert (forwarded_tenant, forwarded_platform, forwarded_channel) == (
        GATEWAY_TENANT,
        PLATFORM_KEY,
        "MSSWWEB",
    )
    # 断言面没有会话上下文，soc-token 不进 repr
    assert ASSERTION not in repr(ChatSessionCredentials(assertion=SecretStr(ASSERTION)))


async def test_gate_rejects_identity_level_denials() -> None:
    """身份级不可用（查无此人 / 用户停用）在取主体阶段直接失败。"""
    expected = {
        "UNBOUND": "EXTERNAL_ASSERTION_UNBOUND",
        "USER_DISABLED": "EXTERNAL_ASSERTION_USER_DISABLED",
    }
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    for reason, code in expected.items():
        principal = (
            None
            if reason == "UNBOUND"
            else ChatPrincipal(
                tenant_id=GATEWAY_TENANT,
                platform_user_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                auth_method="EXTERNAL_ASSERTION",
            )
        )
        console.external_principal_response = ExternalPrincipalResult(
            allowed=False,
            reason=reason,
            principal=principal,  # type: ignore[arg-type]
        )
        with pytest.raises(AppError) as excinfo:
            await gate.resolve_credentials(
                ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
                platform_key=PLATFORM_KEY,
                channel="MSSWWEB",
            )
        assert excinfo.value.code == code, reason


@pytest.mark.parametrize("reason", ["NO_ENTRY_GRANT", "NO_AGENT_GRANT", "AGENT_DISABLED"])
async def test_gate_does_not_reject_entry_level_denials(reason: str) -> None:
    """入口级不可用**不**在取主体阶段失败——否则 `/entries` 会 403 而不是空列表。

    身份是有效的，只是此刻没有可用入口。判定交给具体资源操作
    （`resolve_authorized_entry` 依据 `agents` 集合），`/entries` 自然过滤成空列表。
    """
    console = FakeConsoleClient()
    console.external_principal_response = ExternalPrincipalResult(
        allowed=False,
        reason=reason,  # type: ignore[arg-type]
        principal=ChatPrincipal(
            tenant_id=GATEWAY_TENANT,
            platform_user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            auth_method="EXTERNAL_ASSERTION",
        ),
        agents=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM_KEY,
        channel="MSSWWEB",
    )
    assert identity.agents == (), "入口级拒绝时授权集合为空，资源接口据此拒绝"


async def test_gate_rejects_cross_tenant_assertion_principal() -> None:
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result(_external_identity(tenant_id="another-tenant"))
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    with pytest.raises(AppError) as excinfo:
        await gate.resolve_credentials(
            ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
            platform_key=PLATFORM_KEY,
            channel="MSSWWEB",
        )
    assert excinfo.value.code == "FORBIDDEN"


def test_allowed_result_without_expires_at_is_rejected_by_contract() -> None:
    """契约层就挡住"放行却没有到期时刻"：网关据此给长流设最长时限，不允许缺省。"""
    with pytest.raises(ValueError):
        ExternalPrincipalResult(
            allowed=True,
            principal=ChatPrincipal(
                tenant_id=GATEWAY_TENANT,
                platform_user_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                auth_method="EXTERNAL_ASSERTION",
            ),
            expires_at=None,
        )


async def test_assertion_credentials_without_platform_key_fail_before_console() -> None:
    """平台未配置（surface 无 platform_key）：503 且不触达 Console。"""
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    with pytest.raises(AppError) as excinfo:
        await gate.resolve_credentials(
            ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
            platform_key=None,
            channel="MSSWWEB",
        )
    assert excinfo.value.code == "EXTERNAL_PLATFORM_UNAVAILABLE"
    assert console.external_principal_calls == []


async def test_get_chat_identity_dispatches_by_surface() -> None:
    """依赖层按 surface 分流：断言面走外部断言，`USER_TOKEN` 面走会话核验。"""
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)

    identity = await get_chat_identity(
        gate=gate,
        credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        surface=ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION", platform_key=PLATFORM_KEY),
    )
    assert identity.principal.auth_method == "EXTERNAL_ASSERTION"
    assert len(console.external_principal_calls) == 1
    assert console.chat_principal_calls == []


# --- 长流复核：断言面重放断言原文 -------------------------------------------------------


async def _until_exhausted(iterator: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in iterator]


def _events(*items: str) -> AsyncIterator[str]:
    async def _gen() -> AsyncIterator[str]:
        for item in items:
            yield item

    return _gen()


async def _then_idle(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """先转发给定事件，之后保持空闲（复核定时器有机会到点）。"""
    async for event in events:
        yield event
    await asyncio.Event().wait()


async def _idle_events() -> AsyncIterator[str]:
    """永不产出的事件源：空闲流只能由复核定时器终止。"""
    await asyncio.Event().wait()
    yield ""  # pragma: no cover - 永不产出


async def test_stream_guard_replays_the_assertion_not_a_session_token() -> None:
    """事件流发完后空闲：定时复核重放断言原文，且走外部断言端点而非会话 token 端点。"""
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    credentials = ChatSessionCredentials(assertion=SecretStr(ASSERTION))
    identity = _external_identity()

    guard = ChatStreamGuard(
        gate,
        credentials,
        identity,
        recheck_sec=0.02,
        platform_key=PLATFORM_KEY,
        channel="MSSWWEB",
    )
    consumed = asyncio.ensure_future(_until_exhausted(guard.rechecked(_then_idle(_events("a")))))
    # 事件转发后流空闲：等到至少一次定时复核发生即可收尾。
    await asyncio.sleep(0.12)
    consumed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumed

    assert console.chat_principal_calls == [], "断言面复核不得走会话 token 端点"
    assert console.external_principal_calls, "复核必须重放断言原文"
    for forwarded_assertion, _tenant, platform, channel in console.external_principal_calls:
        assert forwarded_assertion.get_secret_value() == ASSERTION
        assert (platform, channel) == (PLATFORM_KEY, "MSSWWEB")


async def test_stream_guard_stops_assertion_stream_at_expires_at() -> None:
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    guard = ChatStreamGuard(
        gate,
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        _external_identity(expires_in=timedelta(seconds=0)),
        recheck_sec=0.01,
        platform_key=PLATFORM_KEY,
        channel="MSSWWEB",
    )

    with pytest.raises(AppError) as excinfo:
        async for _ in guard.rechecked(_idle_events()):
            pass
    assert excinfo.value.code == "CHAT_SESSION_EXPIRED"


async def test_get_chat_stream_guard_passes_surface_identity() -> None:
    """依赖层把 surface 的 platform_key/channel 交给复核器——否则重放会被当成会话 token。"""
    console = FakeConsoleClient()
    console.external_principal_response = _allowed_result()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    guard = get_chat_stream_guard(
        gate=gate,
        credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        identity=_external_identity(),
        recheck_sec=0.02,
        surface=ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION", platform_key=PLATFORM_KEY),
    )
    consumed = asyncio.ensure_future(_until_exhausted(guard.rechecked(_then_idle(_events("x")))))
    await asyncio.sleep(0.12)
    consumed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumed

    assert console.external_principal_calls, "复核必须发生"
    assert {call[2:] for call in console.external_principal_calls} == {(PLATFORM_KEY, "MSSWWEB")}


# --- API-01 `GET /identity`：权限查询恒 200，拒绝不抛 ------------------------------------

from muad_contracts.webchat import ExternalIdentityGrant, ExternalIdentityView  # noqa: E402
from muad_im_gateway.api.webchat import describe_identity  # noqa: E402
from muad_im_gateway.application.webchat_surfaces import get_webchat_surface  # noqa: E402


def _view(
    *,
    allowed: bool = True,
    reason: str | None = None,
    grants: list[ExternalIdentityGrant] | None = None,
    revision: int | None = 3,
) -> ExternalIdentityView:
    return ExternalIdentityView(
        authenticated=True,
        platform_user_id=uuid.uuid4(),
        external_identity_id=uuid.uuid4(),
        platform_key=PLATFORM_KEY,
        allowed=allowed,
        reason=reason,  # type: ignore[arg-type]
        grants=grants or [],
        auth_revision=revision,
    )


async def test_identity_returns_200_with_allowed_false_instead_of_raising() -> None:
    """权限查询的核心语义：未通过是**正常结果**，不是异常。"""
    console = FakeConsoleClient()
    console.external_identity_response = _view(allowed=False, reason="USER_DISABLED")
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    request = _request("/api/v1/msswwweb/identity", headers={"soc-token": ASSERTION})

    response = await describe_identity(
        request=request,
        credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        gate=gate,
        surface=get_webchat_surface(request),
        entry_id=None,
    )

    payload = response
    data = payload.data if hasattr(payload, "data") else payload["data"]
    assert data["allowed"] is False
    assert data["reason"] == "USER_DISABLED"
    assert data["authenticated"] is True


async def test_identity_forwards_entry_id_and_surface_factors() -> None:
    console = FakeConsoleClient()
    console.external_identity_response = _view()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    request = _request("/api/v1/msswwweb/identity", headers={"soc-token": ASSERTION})
    entry_id = uuid.uuid4()

    await describe_identity(
        request=request,
        credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        gate=gate,
        surface=get_webchat_surface(request),
        entry_id=entry_id,
    )

    forwarded = console.external_identity_calls[0]
    assert forwarded[0].get_secret_value() == ASSERTION
    assert forwarded[1] == GATEWAY_TENANT
    assert forwarded[2:] == (PLATFORM_KEY, "MSSWWEB", entry_id)


async def test_identity_requires_soc_token_not_session_cookie() -> None:
    """断言面没有 `soc-token` 时是 401，不得靠 Cookie 回答「有权限」。"""
    console = FakeConsoleClient()
    console.external_identity_response = _view()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    request = _request(
        "/api/v1/msswwweb/identity",
        cookies={CHAT_SESSION_COOKIE: "session-token"},
    )

    with pytest.raises(AppError) as excinfo:
        await describe_identity(
            request=request,
            credentials=await _credentials_for(request),
            gate=gate,
            surface=get_webchat_surface(request),
            entry_id=None,
        )
    assert excinfo.value.code == "UNAUTHORIZED"
    assert console.external_identity_calls == []


async def test_identity_without_platform_config_fails_before_console() -> None:
    console = FakeConsoleClient()
    console.external_identity_response = _view()
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    request = _request(
        "/api/v1/msswwweb/identity",
        headers={"soc-token": ASSERTION},
        surfaces={"/api/v1/msswwweb": ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION")},
    )

    with pytest.raises(AppError) as excinfo:
        await describe_identity(
            request=request,
            credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
            gate=gate,
            surface=get_webchat_surface(request),
            entry_id=None,
        )
    assert excinfo.value.code == "EXTERNAL_PLATFORM_UNAVAILABLE"
    assert console.external_identity_calls == []


async def test_identity_rejection_from_console_is_not_swallowed() -> None:
    """签名不合法这类「无效请求」仍抛 401 —— 不能被投影成「没有权限」。"""
    console = FakeConsoleClient()
    console.external_identity_error = AppError("EXTERNAL_ASSERTION_INVALID")
    gate = ChatPrincipalGate(console, tenant_id=GATEWAY_TENANT)
    request = _request("/api/v1/msswwweb/identity", headers={"soc-token": ASSERTION})

    with pytest.raises(AppError) as excinfo:
        await describe_identity(
            request=request,
            credentials=ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
            gate=gate,
            surface=get_webchat_surface(request),
            entry_id=None,
        )
    assert excinfo.value.code == "EXTERNAL_ASSERTION_INVALID"


def test_identity_view_rejects_inconsistent_shapes() -> None:
    """契约层守住三种自相矛盾：允许却带原因、拒绝却没原因、未认证却要出结果。"""
    entry = uuid.uuid4()
    with pytest.raises(ValueError):
        ExternalIdentityView(
            authenticated=True,
            platform_key=PLATFORM_KEY,
            allowed=True,
            reason="UNBOUND",
            grants=[],
        )
    with pytest.raises(ValueError):
        ExternalIdentityView(authenticated=True, platform_key=PLATFORM_KEY, allowed=False, grants=[])
    with pytest.raises(ValueError):
        ExternalIdentityView(
            authenticated=False,
            platform_key=PLATFORM_KEY,
            allowed=False,
            reason="UNBOUND",
        )
    with pytest.raises(ValueError):
        ExternalIdentityGrant(
            entry_id=entry,
            agent_id=entry,
            entry_name="e",
            channel="MSSWWEB",
            allowed=False,
            reason=None,
        )
    with pytest.raises(ValueError):
        ExternalIdentityGrant(
            entry_id=entry,
            agent_id=entry,
            entry_name="e",
            channel="MSSWWEB",
            allowed=True,
            reason="AGENT_DISABLED",
        )
