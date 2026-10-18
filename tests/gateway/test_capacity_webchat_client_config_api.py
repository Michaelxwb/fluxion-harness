"""B-257 聊天客户端非敏感配置白名单投影（design §3.4 API-19、§3.7；TASK-057）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/
  错误目录/HTTP 状态），并挂载**生产** `api/webchat.py` 路由（TASK-096 只做装配根注册）；
  装配根状态（Console 客户端、设置客户端、部署租户、启动预算）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 与 `ConsolePlatformSettingsClient` 打真实本地
  uvicorn 服务（真实 socket + 真实封套）：CHAT 主体只经 API-12
  `POST /internal/channel/chat-principal` 核验；`webchat.*` 有效设置与 `revision` 只经
  `GET /internal/v1/platform-settings` 取得 —— **Gateway 不读 PG、不持库**，本文件不碰数据库。
- **真实启动预算**：传输上限/心跳来自进程启动参数（装配根注入的 `app.state` 状态值），与设置
  文档相互独立：指纹用例分别驱动两者，证明 `transport_revision` 只绑定传输投影。

断言要点（B-257）：

- 白名单完备：逐层键集精确（多一个键就红），有效设置原样投影、不复制默认；
- 默认唯一：无设置版本时恰等于 contracts schema 默认，且进程环境变量不是第二个默认来源；
- 敏感零泄漏：内部地址/连接池/额度/密钥/保留期字段与启动硬上限不出现在响应任何位置；
- 配置源坏时明确失败：文档非法/未知分组/版本形状坏/源返回错误/源不可达都不回退默认、
  不回显原始错误细节。
"""

from __future__ import annotations

import ast
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fakes import StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from muad_contracts.platform_settings import default_platform_settings
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b257"
OTHER_TENANT = "tenant-b257-other"
PLATFORM_USER_ID = UUID("9f1c7d24-3b58-4e6a-8c02-5d7e9a1b3c47")
SESSION_ID = UUID("b2575c11-8a4d-4f6e-9b30-2c8d7e5a1f64")
#: CHAT 会话原文 canary：只允许出现在发往 Console 的请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"

CLIENT_CONFIG_URL = "/api/v1/webchat/client-config"
SETTINGS_REVISION = 12
SSE_EVENT_MAX_BYTES = 131072
RUN_EVENT_HEARTBEAT_SEC = 15.0
#: 启动硬预算（装配根注入，§3.7）：只作解析上界，绝不回显给浏览器。
CHAT_MESSAGE_HARD_MAX_BYTES = 777777
CHAT_UPLOAD_HARD_MAX_BYTES = 888888
#: contracts schema 自身的默认硬预算：用 schema 默认验证「无设置版本 ⇒ schema 默认」。
SCHEMA_HARD_MESSAGE_MAX_BYTES = 1048576
SCHEMA_HARD_UPLOAD_MAX_BYTES = 52428800

#: 有效设置（revision 12）：全部落在启动硬预算内，且与 schema 默认逐项不同。
EFFECTIVE_SETTINGS: dict[str, Any] = {
    "webchat": {
        "history_page_size": 75,
        "text_max_bytes": 8192,
        "upload_max_bytes": 123456,
        "upload_max_per_message": 7,
        "notification_poll_sec": 2.5,
        "reconnect_base_ms": 1000,
        "reconnect_cap_ms": 60000,
        "reconnect_max_attempts": 20,
    }
}

#: 敏感/内部 canary：非白名单分组与同组保留期字段，值都不与白名单取值重合。
SENSITIVE_SETTINGS: dict[str, Any] = {
    "agent": {"deadline_ms": 345678},
    "artifact": {"retention_days": 45},
    "auth": {"session_ttl_hours": 23, "max_failed_attempts": 17},
    "capacity": {"max_open_runs_per_tenant": 4321, "max_open_runs_per_user": 21},
    "compaction": {"summary": {"enabled": False, "model_ref": "canary-model-ref-b257"}},
    "model_pool": {"dispatch_epoch_sec": 1234, "input_estimate_multiplier": 1.75},
    "webchat": {
        "history_page_size": 75,
        "text_max_bytes": 8192,
        "upload_max_bytes": 123456,
        "upload_max_per_message": 5,
        "notification_poll_sec": 5.0,
        "reconnect_base_ms": 500,
        "reconnect_cap_ms": 30000,
        "reconnect_max_attempts": 10,
        "access_ttl_days": 29,
        "session_ttl_hours": 11,
        "upload_staging_ttl_sec": 543210,
        "stream_replay_retention_hours": 48,
        "notification_retention_days": 31,
        "history_retention_days": 7,
    },
}
SENSITIVE_VALUES = {
    345678, 45, 23, 17, 4321, 21, 1234, 1.75, 29, 11, 543210, 48, 31, 7,
    CHAT_MESSAGE_HARD_MAX_BYTES, CHAT_UPLOAD_HARD_MAX_BYTES,
}
SENSITIVE_KEYS = {
    "agent", "artifact", "auth", "capacity", "compaction", "model_pool",
    "deadline_ms", "retention_days", "session_ttl_hours", "max_failed_attempts",
    "max_open_runs_per_tenant", "max_open_runs_per_user", "model_ref", "dispatch_epoch_sec",
    "input_estimate_multiplier", "access_ttl_days", "upload_staging_ttl_sec",
    "stream_replay_retention_hours", "notification_retention_days", "history_retention_days",
    "enabled",
}
#: 响应任何位置都不允许出现的内部词干（白名单键值/封套元信息都不含这些词）。
FORBIDDEN_TEXT = (
    "internal", "secret", "credential", "api_key", "dsn", "pool", "quota", "redis",
    "postgres", "sqlalchemy", "localhost", "127.0.0.1", "canary", "access_ttl",
    "session_ttl", "staging_ttl", "retention", "hard_max",
)
WEBCHAT_KEYS = {
    "history_page_size", "text_max_bytes", "upload_max_bytes", "upload_max_per_message",
    "notification_poll_sec",
}
TRANSPORT_KEYS = {
    "sse_event_max_bytes", "sse_heartbeat_sec", "reconnect_base_ms", "reconnect_cap_ms",
    "reconnect_max_attempts",
}

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b257",
        "request_id": "request-b257",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope(None)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _all_keys(node: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(str(key))
            keys |= _all_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _all_keys(item)
    return keys


def _all_values(node: Any) -> list[Any]:
    values: list[Any] = []
    if isinstance(node, dict):
        for value in node.values():
            values.extend(_all_values(value))
    elif isinstance(node, list):
        for item in node:
            values.extend(_all_values(item))
    else:
        values.append(node)
    return values


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ----------------------------------


@dataclass(frozen=True)
class _SettingsCall:
    headers: dict[str, str]


class _ConsoleProbe:
    """API-12 真实 HTTP 桩：chat-principal + platform-settings；记录每次调用的头/体。"""

    def __init__(self, stub: StubConsole) -> None:
        self.principal_tenant = GATEWAY_TENANT
        self.revoked_code: str | None = None
        self.principal_calls = 0
        self.settings_calls: list[_SettingsCall] = []
        self.settings_status = 200
        self.settings_error_code = "COMMON_INTERNAL_ERROR"
        self.settings_data: Any = {"revision": SETTINGS_REVISION, "settings": dict(EFFECTIVE_SETTINGS)}
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal
        stub.handlers[PLATFORM_SETTINGS_PATH] = self._settings

    async def _principal(self, request: Request) -> Response:
        self.principal_calls += 1
        await request.body()
        if self.revoked_code is not None:
            return _error(self.revoked_code, 401)
        return _json(
            {
                "principal": {
                    "tenant_id": self.principal_tenant,
                    "platform_user_id": str(PLATFORM_USER_ID),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": [],
            }
        )

    async def _settings(self, request: Request) -> Response:
        self.settings_calls.append(_SettingsCall(headers=dict(request.headers)))
        if self.settings_status >= 400:
            return _error(self.settings_error_code, self.settings_status)
        return _json(self.settings_data)

    def set_settings(self, *, revision: int, settings: dict[str, Any]) -> None:
        self.settings_data = {"revision": revision, "settings": settings}

    def set_raw_settings(self, payload: Any) -> None:
        self.settings_data = payload


# --- 夹具：真实 HTTP 桩 + 真实 Gateway 应用（按生产装配根状态注入） --------------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def console_probe(stub_console: StubConsole) -> _ConsoleProbe:
    return _ConsoleProbe(stub_console)


_DEFAULT_STATE: dict[str, Any] = {
    "tenant_id": GATEWAY_TENANT,
    "chat_message_hard_max_bytes": CHAT_MESSAGE_HARD_MAX_BYTES,
    "chat_upload_hard_max_bytes": CHAT_UPLOAD_HARD_MAX_BYTES,
    "sse_event_max_bytes": SSE_EVENT_MAX_BYTES,
    "run_event_heartbeat_sec": RUN_EVENT_HEARTBEAT_SEC,
}


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    *,
    cookies: dict[str, str] | None = None,
    settings_base_url: str | None = None,
    **state_overrides: Any,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console 客户端；只注入装配根会发布的状态值。"""
    state = {**_DEFAULT_STATE, **state_overrides}
    names = ("console_client", "settings_client", *state)
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in names}
    console = ConsoleClient(stub_console.url)
    settings = ConsolePlatformSettingsClient(
        settings_base_url or stub_console.url, service_token=INTERNAL_TOKEN
    )
    app.state.console_client = console
    app.state.settings_client = settings
    for name, value in state.items():
        setattr(app.state, name, value)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://gateway.test",
            cookies={CHAT_SESSION_COOKIE: SESSION_TOKEN} if cookies is None else cookies,
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
        await settings.aclose()


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


# --- B-257：CHAT 认证先于任何设置读取 ------------------------------------------------------


async def test_b257_chat_session_and_context_are_required_before_any_settings_read(
    stub_console: StubConsole, console_probe: _ConsoleProbe
) -> None:
    """缺凭据/上下文不符/会话撤销/跨部署租户：都在设置源之前被拒（API-19 仍是 CHAT 面）。"""
    rejected: list[Any] = []
    async with _gateway_client(stub_console, cookies={}) as client:
        rejected.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))
    async with _gateway_client(stub_console) as client:
        rejected.append(await client.get(CLIENT_CONFIG_URL))
        rejected.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers("not-a-uuid")))

    assert [(response.status_code, response.json()["code"]) for response in rejected] == [
        (401, "UNAUTHORIZED"),
        (422, "COMMON_VALIDATION_ERROR"),
        (422, "COMMON_VALIDATION_ERROR"),
    ]
    assert console_probe.settings_calls == [], "门控未通过不得读设置源"
    assert console_probe.principal_calls == 0, "缺凭据不得触达 Console"

    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_client(stub_console) as client:
        revoked = await client.get(CLIENT_CONFIG_URL, headers=_chat_headers())
    assert (revoked.status_code, revoked.json()["code"]) == (401, "CHAT_SESSION_REVOKED")
    assert console_probe.settings_calls == [], "会话撤销不得读设置源"

    console_probe.revoked_code = None
    console_probe.principal_tenant = OTHER_TENANT
    async with _gateway_client(stub_console) as client:
        cross_tenant = await client.get(CLIENT_CONFIG_URL, headers=_chat_headers())
    assert (cross_tenant.status_code, cross_tenant.json()["code"]) == (403, "FORBIDDEN")
    assert console_probe.settings_calls == [], "跨部署租户不得读设置源"


# --- B-257：白名单完备 + 有效设置原样投影 ---------------------------------------------------


async def test_b257_client_config_projects_the_effective_whitelist_and_revisions(
    stub_console: StubConsole, console_probe: _ConsoleProbe
) -> None:
    """有效设置（revision 12）逐字段投影；响应形状精确等于白名单（多一个键就红）。"""
    async with _gateway_client(stub_console) as client:
        response = await client.get(CLIENT_CONFIG_URL, headers=_chat_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    assert response.headers["cache-control"] == "no-store"
    data = body["data"]
    revision = data["transport_revision"]
    assert isinstance(revision, str)
    assert re.fullmatch(r"[0-9a-f]{16,64}", revision), "传输版本必须是不透明指纹（纯十六进制）"

    assert data == {
        "settings_revision": SETTINGS_REVISION,
        "transport_revision": revision,
        "webchat": {
            "history_page_size": 75,
            "text_max_bytes": 8192,
            "upload_max_bytes": 123456,
            "upload_max_per_message": 7,
            "notification_poll_sec": 2.5,
        },
        "transport": {
            "sse_event_max_bytes": SSE_EVENT_MAX_BYTES,
            "sse_heartbeat_sec": RUN_EVENT_HEARTBEAT_SEC,
            "reconnect_base_ms": 1000,
            "reconnect_cap_ms": 60000,
            "reconnect_max_attempts": 20,
        },
    }
    assert set(data) == {"settings_revision", "transport_revision", "webchat", "transport"}
    assert set(data["webchat"]) == WEBCHAT_KEYS
    assert set(data["transport"]) == TRANSPORT_KEYS

    # 设置源只被读一次，且只带服务身份/租户，不带 CHAT 凭据。
    assert len(console_probe.settings_calls) == 1
    settings_headers = console_probe.settings_calls[0].headers
    assert settings_headers["x-tenant-id"] == GATEWAY_TENANT
    assert settings_headers["x-caller-service"] == "gateway"
    assert settings_headers["x-internal-service"] == INTERNAL_TOKEN
    assert "cookie" not in settings_headers and "x-chat-context" not in settings_headers
    # 本路径只触达 Console 的两个受信端点，不触达其他 Owner 面。
    assert set(stub_console.captured_headers) == {CHAT_PRINCIPAL_PATH, PLATFORM_SETTINGS_PATH}


# --- B-257：默认唯一（schema 派生，组件不复制默认值） ---------------------------------------


async def test_b257_client_config_defaults_come_only_from_the_shared_schema(
    stub_console: StubConsole, console_probe: _ConsoleProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无设置版本 ⇒ 恰等于 contracts schema 默认；进程环境变量不是第二个默认来源。"""
    monkeypatch.setenv("WEBCHAT_HISTORY_PAGE_SIZE", "999")
    monkeypatch.setenv("WEBCHAT_TEXT_MAX_BYTES", "999999")
    console_probe.set_settings(revision=0, settings={})

    async with _gateway_client(
        stub_console,
        chat_message_hard_max_bytes=SCHEMA_HARD_MESSAGE_MAX_BYTES,
        chat_upload_hard_max_bytes=SCHEMA_HARD_UPLOAD_MAX_BYTES,
    ) as client:
        response = await client.get(CLIENT_CONFIG_URL, headers=_chat_headers())

    defaults = default_platform_settings().webchat
    data = response.json()["data"]
    assert data["settings_revision"] == 0
    assert data["webchat"] == {
        "history_page_size": defaults.history_page_size,
        "text_max_bytes": defaults.text_max_bytes,
        "upload_max_bytes": defaults.upload_max_bytes,
        "upload_max_per_message": defaults.upload_max_per_message,
        "notification_poll_sec": defaults.notification_poll_sec,
    }
    assert data["transport"] == {
        "sse_event_max_bytes": SSE_EVENT_MAX_BYTES,
        "sse_heartbeat_sec": RUN_EVENT_HEARTBEAT_SEC,
        "reconnect_base_ms": defaults.reconnect_base_ms,
        "reconnect_cap_ms": defaults.reconnect_cap_ms,
        "reconnect_max_attempts": defaults.reconnect_max_attempts,
    }
    assert data["webchat"]["history_page_size"] != 999, "环境变量不得成为第二个默认来源"


# --- B-257：transport_revision 只绑定传输投影 -----------------------------------------------


async def test_b257_transport_revision_binds_exactly_the_transport_projection(
    stub_console: StubConsole, console_probe: _ConsoleProbe
) -> None:
    """同参数确定性稳定；启动预算或传输设置变化即变；非传输设置变化不变。"""
    async with _gateway_client(stub_console) as first:
        baseline = (await first.get(CLIENT_CONFIG_URL, headers=_chat_headers())).json()["data"]
    async with _gateway_client(stub_console) as second:
        same = (await second.get(CLIENT_CONFIG_URL, headers=_chat_headers())).json()["data"]
    assert same["transport_revision"] == baseline["transport_revision"], "指纹必须确定可复现"

    async with _gateway_client(stub_console, sse_event_max_bytes=SSE_EVENT_MAX_BYTES * 2) as third:
        budget_changed = (await third.get(CLIENT_CONFIG_URL, headers=_chat_headers())).json()["data"]
    assert budget_changed["transport_revision"] != baseline["transport_revision"]
    assert budget_changed["settings_revision"] == baseline["settings_revision"]
    assert budget_changed["transport"]["sse_event_max_bytes"] == SSE_EVENT_MAX_BYTES * 2

    non_transport = {
        **EFFECTIVE_SETTINGS,
        "webchat": {**EFFECTIVE_SETTINGS["webchat"], "history_page_size": 25},
    }
    console_probe.set_settings(revision=SETTINGS_REVISION + 1, settings=non_transport)
    async with _gateway_client(stub_console) as fourth:
        settings_changed = (await fourth.get(CLIENT_CONFIG_URL, headers=_chat_headers())).json()["data"]
    assert settings_changed["settings_revision"] == SETTINGS_REVISION + 1
    assert settings_changed["transport_revision"] == baseline["transport_revision"], (
        "非传输设置变化不得改变传输绑定"
    )
    assert settings_changed["webchat"]["history_page_size"] == 25

    transport_changed = {
        **EFFECTIVE_SETTINGS,
        "webchat": {**EFFECTIVE_SETTINGS["webchat"], "reconnect_base_ms": 2000},
    }
    console_probe.set_settings(revision=SETTINGS_REVISION + 2, settings=transport_changed)
    async with _gateway_client(stub_console) as fifth:
        rebound = (await fifth.get(CLIENT_CONFIG_URL, headers=_chat_headers())).json()["data"]
    assert rebound["transport_revision"] != baseline["transport_revision"], (
        "传输设置变化必须重新绑定传输版本"
    )
    assert rebound["transport"]["reconnect_base_ms"] == 2000


# --- B-257：敏感/内部字段零泄漏 --------------------------------------------------------------


async def test_b257_client_config_never_carries_internal_or_sensitive_fields(
    stub_console: StubConsole, console_probe: _ConsoleProbe
) -> None:
    """同租户设置文档里的敏感分组/保留期字段/启动硬上限与 CHAT 凭据都不进响应。"""
    console_probe.set_settings(revision=SETTINGS_REVISION, settings=dict(SENSITIVE_SETTINGS))
    async with _gateway_client(stub_console) as client:
        response = await client.get(CLIENT_CONFIG_URL, headers=_chat_headers())

    body_text = response.text
    data = response.json()["data"]
    assert set(data["webchat"]) == WEBCHAT_KEYS
    assert set(data["transport"]) == TRANSPORT_KEYS
    assert SENSITIVE_KEYS.isdisjoint(_all_keys(data)), "敏感字段不得有键位"
    assert set(_all_values(data)).isdisjoint(SENSITIVE_VALUES), "敏感取值不得出现在投影里"
    lowered = body_text.lower()
    for stem in FORBIDDEN_TEXT:
        assert stem not in lowered, f"响应不得包含 {stem!r}"
    assert SESSION_TOKEN not in body_text, "CHAT 会话凭据不得回显"
    assert INTERNAL_TOKEN not in body_text, "内部服务身份不得回显"


# --- B-257：配置源坏时明确失败（不回退默认、不回显原始细节） ---------------------------------


async def test_b257_client_config_fails_explicitly_when_the_settings_source_is_broken(
    stub_console: StubConsole, console_probe: _ConsoleProbe
) -> None:
    """文档非法/未知分组/版本形状坏/源返回错误/源不可达：明确失败且 data 为空。"""
    failures: list[Any] = []
    console_probe.set_settings(revision=SETTINGS_REVISION, settings={"webchat": {"history_page_size": 0}})
    async with _gateway_client(stub_console) as client:
        failures.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))

    console_probe.set_settings(
        revision=SETTINGS_REVISION,
        settings={"webchat": {}, "model_pool_secret": {"api_key": "canary-key"}},
    )
    async with _gateway_client(stub_console) as client:
        failures.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))

    console_probe.set_raw_settings({"revision": "12", "settings": {}})
    async with _gateway_client(stub_console) as client:
        failures.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))

    console_probe.settings_status = 503
    console_probe.settings_error_code = "PLATFORM_SETTINGS_UNAVAILABLE"
    async with _gateway_client(stub_console) as client:
        failures.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))

    console_probe.settings_status = 200
    async with _gateway_client(stub_console, settings_base_url="http://127.0.0.1:1") as client:
        failures.append(await client.get(CLIENT_CONFIG_URL, headers=_chat_headers()))

    assert [(response.status_code, response.json()["code"]) for response in failures] == [
        (500, "COMMON_INTERNAL_ERROR"),
        (500, "COMMON_INTERNAL_ERROR"),
        (500, "COMMON_INTERNAL_ERROR"),
        (503, "PLATFORM_SETTINGS_UNAVAILABLE"),
        (500, "COMMON_INTERNAL_ERROR"),
    ]
    for response in failures:
        body = response.json()
        assert body["data"] is None, "失败不得回退默认配置"
        assert "history_page_size" not in response.text
        assert "model_pool_secret" not in response.text
        assert "canary" not in response.text.lower()


# --- B-257：网关不持库、配置模块无第二来源 ---------------------------------------------------


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_b257_client_config_modules_hold_no_database_and_no_second_source() -> None:
    """网关不持库：接口/配置模块零 sqlalchemy；配置模块不读环境、不依赖启动设置单例。"""
    root = Path(__file__).resolve().parents[2]
    relative_paths = (
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
        "apps/im-gateway/src/muad_im_gateway/application/webchat_config.py",
    )
    for relative in relative_paths:
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        assert not any(
            "sqlalchemy" in module for module in _imported_modules(source)
        ), relative

    config_source = (root / relative_paths[1]).read_text(encoding="utf-8")
    config_modules = _imported_modules(config_source)
    assert not any(module.startswith("muad_common") for module in config_modules), (
        "启动设置单例必须由装配根注入，不在配置模块里构造"
    )
    assert "os" not in config_modules, "配置模块不得读进程环境"
    for forbidden in ("os.environ", "getenv"):
        assert forbidden not in config_source, f"{relative_paths[1]} 不得包含 {forbidden!r}"
