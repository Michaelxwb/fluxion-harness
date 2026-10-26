"""B-270 Gateway 通知查询与显式已读接口（design §3.4 API-11、§3.6；TASK-070）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/
  错误目录、真实 `api/deps.py` CHAT 门控），并挂载**生产** `api/webchat.py` 路由（TASK-096 只做
  装配根注册）；装配根状态（Console/Runtime 客户端、部署租户）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 打真实本地 uvicorn 服务（真实 socket），按
  API-12 `POST /internal/channel/chat-principal` 应答；CHAT 主体只从响应导出。
- **真实 Runtime HTTP（Owner）**：`RuntimeClient` 打真实本地 uvicorn 服务；`GET
  /internal/webchat/notifications`、`POST .../{id}/read` 是真实内部端点探针（真实封套、真实
  `X-Internal-Service` 门控、真实 feed seq 增量与显式已读语义），通知事实只存在于 Owner 侧。
- **不读写 DB**：本文件不碰任何数据库；`tests/architecture/test_im_gateway_boundaries.py` 机检
  网关零 sqlalchemy，本文件另对通知接口模块做同样的静态断言。

断言要点（B-270）：

- CHAT Cookie + `X-Chat-Context` 与部署租户门控先于任何 Owner 触达；主体只经 Console 核验，
  客户端自报 `user_id` 在受信调用里没有位置；
- API-11 逐字段等于 Owner 的公开投影（安全摘要/引用/稳定游标）；含投递/租约/存储内部字段的
  坏封套显式失败，内部字段永不进入公开响应；
- 增量游标 `after_seq` 原样交给 Owner 裁决：`CURSOR_AHEAD` 连同真实 feed 水位显式返回，
  不静默夹取成空页；坏游标在网关侧拒绝（不触达 Owner）；
- 显式已读回执 `{id, read_at}` 幂等、与送达状态独立；每次读取都经 Owner（网关不缓存通知权威）。
"""

from __future__ import annotations

import ast
import json
import socket
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import uvicorn
from fakes import StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from muad_im_core import BotSnapshotCache
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b270"
OTHER_TENANT = "tenant-b270-other"
PLATFORM_USER_ID = UUID("7d2f9b31-4c8a-4e5d-9f10-6a3b8c2d4e51")
OTHER_USER_ID = UUID("1c9e5a73-2f84-4b06-9d31-8e5c7a2b4f60")
SESSION_ID = UUID("c1a4e8f2-6b30-4d7a-8e59-0f2b3c4d5e61")
ENTRY_ID = UUID("e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b")
AGENT_ID = UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
CONVERSATION_ID = UUID("b4c5d6e7-f8a9-4b0c-9d1e-2f3a4b5c6d7e")
RUN_ID = UUID("c5d6e7f8-a9b0-4c1d-8e2f-3a4b5c6d7e8f")
TASK_ID = UUID("d6e7f8a9-b0c1-4d2e-9f3a-4b5c6d7e8f90")
EVENT_ID = UUID("e7f8a9b0-c1d2-4e3f-8a4b-5c6d7e8f9012")
ARTIFACT_ID = UUID("f8a9b0c1-d2e3-4f4a-9b5c-6d7e8f901234")
NOTIFICATION_1 = UUID("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
NOTIFICATION_2 = UUID("1b2c3d4e-5f60-4b7c-9d8e-0f1a2b3c4d5e")
NOTIFICATION_3 = UUID("2c3d4e5f-6a70-4c8d-8e9f-1a2b3c4d5e6f")
NOTIFICATION_OTHER = UUID("3d4e5f6a-7b80-4d9e-9f0a-2b3c4d5e6f70")
#: CHAT 会话原文 canary：只允许出现在发往 Console 的核验请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"
#: 他用户通知 canary：客户端自报 user_id 不得读到它。
OTHER_USER_CANARY = "other-user-canary-b270"
#: Owner 侧投递/租约/存储内部事实的 canary：永不进入公开投影。
DELIVERY_KEY_CANARY = "delivery-key-canary-b270"
STORAGE_KEY_CANARY = "storage-key-canary-b270"
LEASE_OWNER_CANARY = "lease-owner-canary-b270"

NOTIFICATIONS_PATH = "/internal/webchat/notifications"
NOTIFICATIONS_URL = "/api/v1/webchat/notifications"
CREATED_AT_1 = "2026-10-18T06:00:00Z"
CREATED_AT_2 = "2026-10-18T06:05:00Z"
CREATED_AT_3 = "2026-10-18T06:10:00Z"
READ_AT = "2026-10-18T07:05:00Z"
SUMMARY_RUN_COMPLETED = "会话有新结果"
SUMMARY_TASK_RESULT = "后台任务已完成"
SUMMARY_RUN_WAITING_INPUT = "有新的待确认"
ATTACHMENT = {
    "artifact_id": str(ARTIFACT_ID),
    "kind": "DOCUMENT",
    "media_type": "application/pdf",
    "size": 2048,
    "filename": "报告.pdf",
    "checksum": "sha256:" + "ab" * 32,
    "available": True,
}
#: 通知 DTO 的完整公开字段（安全投影：多一个字段都算泄露内部事实）。
NOTIFICATION_FIELDS = {
    "id",
    "seq",
    "entry_id",
    "agent_id",
    "conversation_id",
    "run_id",
    "task_id",
    "event_id",
    "kind",
    "summary",
    "attachments",
    "created_at",
    "read_at",
}
PAGE_FIELDS = {"items", "page", "page_size", "total", "through_seq", "next_seq", "unread_count"}
FORBIDDEN_FIELDS = {
    "delivery_state",
    "delivery_key",
    "delivery_attempt",
    "next_attempt_at",
    "lease_owner",
    "lease_epoch",
    "lease_until",
    "storage_key",
    "payload",
}
CONSOLE_ROLE_FIELDS = {"role", "roles", "permissions", "username", "account_id", "user_code"}

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b270",
        "request_id": "request-b270",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int, data: Any = None) -> JSONResponse:
    body = _envelope(data)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _read_url(notification_id: UUID) -> str:
    return f"{NOTIFICATIONS_URL}/{notification_id}/read"


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


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ----------------------------------


class _ConsoleProbe:
    """API-12 的真实 HTTP 桩：chat-principal；记录调用次数并按脚本裁决。"""

    def __init__(self, stub: StubConsole) -> None:
        self.principal_calls = 0
        self.revoked_code: str | None = None
        self.principal_tenant = GATEWAY_TENANT
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal

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
                "expires_at": "2026-10-18T19:00:00Z",
                "agents": [],
            }
        )


# --- 真实 Runtime Owner 接口桩（真实 socket + 真实封套） -----------------------------------


@dataclass(frozen=True)
class _Call:
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class _Notification:
    """Owner 侧通知行：含投递/租约/存储内部事实（永不进入公开投影）。"""

    id: UUID
    seq: int
    user_id: UUID
    kind: str
    summary: str
    created_at: str
    task_id: UUID | None = None
    event_id: UUID | None = None
    attachments: list[dict[str, Any]] | None = None
    read_at: str | None = None
    delivery_state: str = "NOT_REQUIRED"
    delivery_key: str = DELIVERY_KEY_CANARY
    storage_key: str = STORAGE_KEY_CANARY


class _RuntimeProbe:
    """Runtime 内部通知端点桩：真实 HTTP + 真实服务身份门控 + 真实 feed seq/已读语义。

    通知权威只在这里（Owner 侧）：网关每次读取都触达本探针，探针状态变化立即反映到响应。
    """

    def __init__(self) -> None:
        self.calls: dict[str, list[_Call]] = defaultdict(list)
        self.notifications: list[_Notification] = [
            _Notification(
                id=NOTIFICATION_1,
                seq=1,
                user_id=PLATFORM_USER_ID,
                kind="RUN_COMPLETED",
                summary=SUMMARY_RUN_COMPLETED,
                created_at=CREATED_AT_1,
                event_id=EVENT_ID,
                attachments=[],
            ),
            _Notification(
                id=NOTIFICATION_2,
                seq=2,
                user_id=PLATFORM_USER_ID,
                kind="TASK_RESULT",
                summary=SUMMARY_TASK_RESULT,
                created_at=CREATED_AT_2,
                task_id=TASK_ID,
                attachments=[dict(ATTACHMENT)],
            ),
            _Notification(
                id=NOTIFICATION_3,
                seq=3,
                user_id=PLATFORM_USER_ID,
                kind="RUN_WAITING_INPUT",
                summary=SUMMARY_RUN_WAITING_INPUT,
                created_at=CREATED_AT_3,
                event_id=EVENT_ID,
                attachments=[],
            ),
            _Notification(
                id=NOTIFICATION_OTHER,
                seq=1,
                user_id=OTHER_USER_ID,
                kind="RUN_COMPLETED",
                summary=OTHER_USER_CANARY,
                created_at=CREATED_AT_1,
                attachments=[],
            ),
        ]
        #: 坏封套注入：Owner 错误地把内部字段混进公开投影（网关必须显式失败）。
        self.internal_fields = False
        #: 坏封套注入：`next_seq > through_seq`（契约不变量被破坏）。
        self.bad_next_seq = False
        #: 坏封套注入：200 却没有 data。
        self.data_none = False
        self._app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # --- 记录与状态 -----------------------------------------------------------------
    def _record(self, path: str, request: Request, body: dict[str, Any] | None = None) -> None:
        self.calls[path].append(
            _Call(
                path=path,
                query=dict(request.query_params),
                headers=dict(request.headers),
                body=body or {},
            )
        )

    @property
    def list_calls(self) -> list[_Call]:
        return self.calls[NOTIFICATIONS_PATH]

    def append_notification(self) -> None:
        """Owner 侧新增一条通知：网关响应必须立刻跟上（无第二份权威）。"""
        self.notifications.append(
            _Notification(
                id=UUID("4e5f6a7b-8c90-4eaf-8a1b-3c4d5e6f7081"),
                seq=4,
                user_id=PLATFORM_USER_ID,
                kind="RUN_COMPLETED",
                summary=SUMMARY_RUN_COMPLETED,
                created_at="2026-10-18T06:15:00Z",
                event_id=EVENT_ID,
                attachments=[],
            )
        )

    def _through_seq(self, user_id: UUID) -> int:
        return max((n.seq for n in self.notifications if n.user_id == user_id), default=0)

    def _item(self, notification: _Notification) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": str(notification.id),
            "seq": notification.seq,
            "entry_id": str(ENTRY_ID),
            "agent_id": str(AGENT_ID),
            "conversation_id": str(CONVERSATION_ID),
            "run_id": str(RUN_ID),
            "task_id": str(notification.task_id) if notification.task_id else None,
            "event_id": str(notification.event_id) if notification.event_id else None,
            "kind": notification.kind,
            "summary": notification.summary,
            "attachments": [dict(a) for a in notification.attachments or []],
            "created_at": notification.created_at,
            "read_at": notification.read_at,
        }
        if self.internal_fields:
            item.update(
                {
                    "delivery_state": notification.delivery_state,
                    "delivery_key": notification.delivery_key,
                    "lease_owner": LEASE_OWNER_CANARY,
                    "lease_epoch": 7,
                    "lease_until": "2026-10-18T08:00:00Z",
                    "storage_key": notification.storage_key,
                }
            )
        return item

    def _page(self, user_id: UUID, *, after_seq: int, page: int, page_size: int) -> JSONResponse:
        through_seq = self._through_seq(user_id)
        if after_seq > through_seq:
            return _error(
                "CURSOR_AHEAD", 400, {"after_seq": after_seq, "through_seq": through_seq}
            )
        window = [
            n for n in self.notifications if n.user_id == user_id and after_seq < n.seq <= through_seq
        ]
        total = len(window)
        rows = window[(page - 1) * page_size : (page - 1) * page_size + page_size]
        scanned = (page - 1) * page_size + len(rows)
        next_seq = rows[-1].seq if rows and scanned < total else through_seq
        if self.bad_next_seq:
            next_seq = through_seq + 1
        unread = sum(
            1 for n in self.notifications if n.user_id == user_id and n.read_at is None
        )
        return _json(
            {
                "items": [self._item(n) for n in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "through_seq": through_seq,
                "next_seq": next_seq,
                "unread_count": unread,
            }
        )

    def _read(self, notification_id: str, user_id: str) -> JSONResponse:
        row = next(
            (
                n
                for n in self.notifications
                if str(n.id) == notification_id and str(n.user_id) == user_id
            ),
            None,
        )
        if row is None:
            return _error("COMMON_NOT_FOUND", 404)
        if row.read_at is None:
            row.read_at = READ_AT
        return _json({"id": str(row.id), "read_at": row.read_at})

    # --- 端点（与 Runtime `api/webchat_history.py` 同形） -----------------------------
    def _build_app(self) -> Any:
        from fastapi import FastAPI

        runtime_app = FastAPI()

        def _authorized(request: Request) -> bool:
            return request.headers.get("x-internal-service") == INTERNAL_TOKEN

        @runtime_app.get(NOTIFICATIONS_PATH)
        async def _list(request: Request) -> Response:
            self._record(NOTIFICATIONS_PATH, request)
            if not _authorized(request):
                return _error("FORBIDDEN", 403)
            if self.data_none:
                return JSONResponse(status_code=200, content={"code": "0", "msg": "ok", "data": None})
            query = request.query_params
            user_id = UUID(query["user_id"])
            return self._page(
                user_id,
                after_seq=int(query.get("after_seq", "0")),
                page=int(query.get("page", "1")),
                page_size=int(query.get("page_size", "50")),
            )

        @runtime_app.post(f"{NOTIFICATIONS_PATH}/{{notification_id}}/read")
        async def _read_endpoint(notification_id: str, request: Request) -> Response:
            self._record(
                f"{NOTIFICATIONS_PATH}/{notification_id}/read",
                request,
                json.loads(await request.body() or b"{}"),
            )
            if not _authorized(request):
                return _error("FORBIDDEN", 403)
            return self._read(notification_id, request.query_params.get("user_id", ""))

        return runtime_app

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime probe did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


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


@pytest.fixture
async def runtime_probe() -> AsyncIterator[_RuntimeProbe]:
    probe = _RuntimeProbe()
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    *,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console/Runtime 客户端；只注入装配根会发布的状态值。"""
    previous: dict[str, Any] = {
        name: getattr(app.state, name, None)
        for name in ("console_client", "runtime_client", "bot_snapshot", "tenant_id")
    }
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.bot_snapshot = BotSnapshotCache(console, tenant_id=GATEWAY_TENANT)
    app.state.tenant_id = GATEWAY_TENANT
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
        await runtime.aclose()


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


# --- B-270：CHAT 门控先于任何 Owner 触达 ----------------------------------------------------


async def test_b270_chat_gate_precedes_every_notification_owner_call(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """缺凭据/坏上下文/被撤销/跨部署租户：都在 Runtime 之前被拒（通知仍是 CHAT 面）。"""
    rejected: list[Any] = []
    async with _gateway_client(stub_console, runtime_probe, cookies={}) as client:
        rejected.append(await client.get(NOTIFICATIONS_URL, headers=_chat_headers()))
        rejected.append(await client.post(_read_url(NOTIFICATION_1), headers=_chat_headers()))
    async with _gateway_client(stub_console, runtime_probe) as client:
        rejected.append(await client.get(NOTIFICATIONS_URL))
        rejected.append(await client.get(NOTIFICATIONS_URL, headers=_chat_headers("not-a-uuid")))
    assert [(r.status_code, r.json()["code"]) for r in rejected] == [
        (401, "UNAUTHORIZED"),
        (401, "UNAUTHORIZED"),
        (422, "COMMON_VALIDATION_ERROR"),
        (422, "COMMON_VALIDATION_ERROR"),
    ]
    assert runtime_probe.calls == {}, "门控未通过时 Runtime 零触达"
    assert console_probe.principal_calls == 0, "缺凭据/坏上下文不得触达 Console"

    # 被撤销的 CHAT 会话：Console 明确拒绝，网关原样上抛，不触达 Runtime。
    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_client(stub_console, runtime_probe) as client:
        revoked = await client.post(_read_url(NOTIFICATION_1), headers=_chat_headers())
    assert (revoked.status_code, revoked.json()["code"]) == (401, "CHAT_SESSION_REVOKED")
    assert runtime_probe.calls == {}
    assert console_probe.principal_calls == 1, "撤销判定只经 Console"

    # 上下文不符：网关自拒（409），不触达 Runtime。
    console_probe.revoked_code = None
    async with _gateway_client(stub_console, runtime_probe) as client:
        mismatch = await client.get(NOTIFICATIONS_URL, headers=_chat_headers(uuid.uuid4()))
    assert (mismatch.status_code, mismatch.json()["code"]) == (409, "AUTH_CONTEXT_CHANGED")
    assert runtime_probe.calls == {}

    # 跨部署租户：403，不把 Token 认证误实现成跨租户 Gateway。
    console_probe.principal_tenant = OTHER_TENANT
    async with _gateway_client(stub_console, runtime_probe) as client:
        cross_tenant = await client.get(NOTIFICATIONS_URL, headers=_chat_headers())
    assert (cross_tenant.status_code, cross_tenant.json()["code"]) == (403, "FORBIDDEN")
    assert runtime_probe.calls == {}
    assert SESSION_TOKEN not in revoked.text + mismatch.text + cross_tenant.text


# --- B-270：API-11 列表逐字段等于 Owner 公开投影 ---------------------------------------------


async def test_b270_notification_page_projects_the_owner_public_projection(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """列表响应恰为 Owner 的安全公开投影（摘要/引用/水位）；内部字段与凭据零泄漏。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        response = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"page": 1, "page_size": 2}
        )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store", "私有聊天资源必须 no-store"
    data = response.json()["data"]
    assert set(data) == PAGE_FIELDS
    assert data == {
        "items": [
            {
                "id": str(NOTIFICATION_1),
                "seq": 1,
                "entry_id": str(ENTRY_ID),
                "agent_id": str(AGENT_ID),
                "conversation_id": str(CONVERSATION_ID),
                "run_id": str(RUN_ID),
                "task_id": None,
                "event_id": str(EVENT_ID),
                "kind": "RUN_COMPLETED",
                "summary": SUMMARY_RUN_COMPLETED,
                "attachments": [],
                "created_at": CREATED_AT_1,
                "read_at": None,
            },
            {
                "id": str(NOTIFICATION_2),
                "seq": 2,
                "entry_id": str(ENTRY_ID),
                "agent_id": str(AGENT_ID),
                "conversation_id": str(CONVERSATION_ID),
                "run_id": str(RUN_ID),
                "task_id": str(TASK_ID),
                "event_id": None,
                "kind": "TASK_RESULT",
                "summary": SUMMARY_TASK_RESULT,
                "attachments": [ATTACHMENT],
                "created_at": CREATED_AT_2,
                "read_at": None,
            },
        ],
        "page": 1,
        "page_size": 2,
        "total": 3,
        "through_seq": 3,
        "next_seq": 2,
        "unread_count": 3,
    }
    assert set(data["items"][0]) == NOTIFICATION_FIELDS, "通知投影恰为公开字段集"
    assert FORBIDDEN_FIELDS.isdisjoint(_all_keys(data)), "投递/租约/存储内部字段不得有键位"
    assert CONSOLE_ROLE_FIELDS.isdisjoint(_all_keys(data)), "Console 角色字段不得出现"
    for canary in (DELIVERY_KEY_CANARY, STORAGE_KEY_CANARY, LEASE_OWNER_CANARY):
        assert canary not in response.text

    # 完整转 Owner：主体来自受信 CHAT 身份，页参数原样透传；不携带任何浏览器凭据。
    assert console_probe.principal_calls == 1
    call = runtime_probe.list_calls[0]
    assert call.query == {
        "user_id": str(PLATFORM_USER_ID),
        "after_seq": "0",
        "page": "1",
        "page_size": "2",
    }
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert "cookie" not in call.headers and "x-chat-context" not in call.headers
    assert SESSION_TOKEN not in response.text and INTERNAL_TOKEN not in response.text


# --- B-270：受信主体是唯一身份；客户端自报 user_id 没有位置 -----------------------------------


async def test_b270_verified_subject_is_the_only_notification_identity(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """客户端自报 `user_id` 不进入受信调用：读到的只能是被核验主体自己的 feed。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        response = await client.get(
            NOTIFICATIONS_URL,
            headers=_chat_headers(),
            params={"user_id": str(OTHER_USER_ID), "after_seq": 0, "page_size": 10},
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["summary"] for item in data["items"]] == [
        SUMMARY_RUN_COMPLETED,
        SUMMARY_TASK_RESULT,
        SUMMARY_RUN_WAITING_INPUT,
    ]
    assert OTHER_USER_CANARY not in response.text, "他用户的 feed 不得被客户端自报身份读到"
    assert runtime_probe.list_calls[0].query["user_id"] == str(PLATFORM_USER_ID)


# --- B-270：增量游标/offset 页透传；超前游标显式返回真实水位 ---------------------------------


async def test_b270_incremental_cursor_and_offset_page_follow_the_owner(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """`after_seq` 原样交给 Owner 裁决：增量/offset 页正确，`CURSOR_AHEAD` 显式不夹取。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        incremental = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"after_seq": 2, "page_size": 10}
        )
        offset = await client.get(
            NOTIFICATIONS_URL,
            headers=_chat_headers(),
            params={"after_seq": 0, "page": 2, "page_size": 1},
        )
        ahead = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"after_seq": 99}
        )
        bad = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"after_seq": -1}
        )

    assert incremental.status_code == 200, incremental.text
    inc = incremental.json()["data"]
    assert [item["seq"] for item in inc["items"]] == [3], "增量读取只回水位之后的条目"
    assert inc["through_seq"] == 3 and inc["next_seq"] == 3 and inc["unread_count"] == 3

    assert offset.status_code == 200, offset.text
    off = offset.json()["data"]
    assert [item["seq"] for item in off["items"]] == [2]
    assert off["page"] == 2 and off["page_size"] == 1 and off["total"] == 3
    assert off["next_seq"] == 2, "本页未追平水位时 next_seq 是本页最后一条"

    assert ahead.status_code == 400, ahead.text
    assert ahead.json()["code"] == "CURSOR_AHEAD"
    assert ahead.json()["data"] == {"after_seq": 99, "through_seq": 3}, "真实水位原样带出"
    assert "items" not in ahead.text, "超前游标不得被夹取成空页"

    assert bad.status_code == 422 and bad.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert [call.query["after_seq"] for call in runtime_probe.list_calls] == ["2", "0", "99"], (
        "合法游标原样交给 Owner；坏游标在网关侧拒绝"
    )


# --- B-270：显式已读回执幂等、与送达状态独立 -------------------------------------------------


async def test_b270_explicit_read_receipt_is_idempotent_and_delivery_independent(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """已读回执恰为 `{id, read_at}`；重复标记返回原时间；只影响明确读过的行。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        first = await client.post(_read_url(NOTIFICATION_1), headers=_chat_headers())
        repeat = await client.post(_read_url(NOTIFICATION_1), headers=_chat_headers())
        unknown = await client.post(_read_url(uuid.uuid4()), headers=_chat_headers())
        foreign = await client.post(_read_url(NOTIFICATION_OTHER), headers=_chat_headers())
        after = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10}
        )

    assert first.status_code == 200, first.text
    receipt = first.json()["data"]
    assert receipt == {"id": str(NOTIFICATION_1), "read_at": READ_AT}, "与前端已锁形状一致"
    assert set(receipt) == {"id", "read_at"}
    assert FORBIDDEN_FIELDS.isdisjoint(_all_keys(receipt))

    assert repeat.status_code == 200 and repeat.json()["data"] == receipt, "重复标记幂等"
    read_path = f"{NOTIFICATIONS_PATH}/{NOTIFICATION_1}/read"
    assert len(runtime_probe.calls[read_path]) == 2, "两次标记都经 Owner；网关不留已读副本"
    read_call = runtime_probe.calls[read_path][0]
    assert read_call.query == {"user_id": str(PLATFORM_USER_ID)}
    assert read_call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert read_call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert "cookie" not in read_call.headers and "x-chat-context" not in read_call.headers

    assert unknown.status_code == 404 and unknown.json()["code"] == "COMMON_NOT_FOUND"
    assert foreign.status_code == 404 and foreign.json()["code"] == "COMMON_NOT_FOUND", (
        "他用户的通知按主体核属不可见（客户端无法借网关读到）"
    )
    assert OTHER_USER_CANARY not in foreign.text

    assert after.status_code == 200, after.text
    page = after.json()["data"]
    assert page["unread_count"] == 2, "只标记明确读过的行；其他未读不受影响"
    assert [item["read_at"] for item in page["items"]] == [READ_AT, None, None]
    # 已读与送达是两条独立事实：已读不改变 Owner 侧投递状态（网关也没碰它）。
    assert runtime_probe.notifications[0].delivery_state == "NOT_REQUIRED"
    assert runtime_probe.notifications[0].delivery_key == DELIVERY_KEY_CANARY


# --- B-270：含内部字段/坏形状的 Owner 封套显式失败 -------------------------------------------


async def test_b270_internal_fields_or_broken_envelopes_fail_explicitly(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """Owner 把投递/租约字段混进投影、`next_seq` 越界或缺 data：显式失败不转发内部事实。"""
    failures: list[Any] = []
    runtime_probe.internal_fields = True
    async with _gateway_client(stub_console, runtime_probe) as client:
        failures.append(
            await client.get(NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10})
        )

    runtime_probe.internal_fields = False
    runtime_probe.bad_next_seq = True
    async with _gateway_client(stub_console, runtime_probe) as client:
        failures.append(
            await client.get(NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10})
        )

    runtime_probe.bad_next_seq = False
    runtime_probe.data_none = True
    async with _gateway_client(stub_console, runtime_probe) as client:
        failures.append(
            await client.get(NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10})
        )

    assert [(r.status_code, r.json()["code"]) for r in failures] == [
        (500, "COMMON_INTERNAL_ERROR"),
        (500, "COMMON_INTERNAL_ERROR"),
        (500, "COMMON_INTERNAL_ERROR"),
    ]
    for response in failures:
        assert response.json()["data"] is None, "失败不得回退默认投影"
        lowered = response.text.lower()
        for stem in (
            "delivery_state",
            "delivery_key",
            "lease_owner",
            "lease_epoch",
            "lease_until",
            "storage_key",
            "canary",
        ):
            assert stem not in lowered, f"坏封套失败不得回显 {stem!r}"


# --- B-270：网关不建第二份通知权威 -----------------------------------------------------------


async def test_b270_gateway_keeps_no_second_notification_authority(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
) -> None:
    """每次读取都经 Owner：Owner 侧新增通知后，网关响应立刻跟上（无缓存/无影子副本）。"""
    async with _gateway_client(stub_console, runtime_probe) as client:
        first = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10}
        )
        runtime_probe.append_notification()
        second = await client.get(
            NOTIFICATIONS_URL, headers=_chat_headers(), params={"page_size": 10}
        )

    assert first.json()["data"]["total"] == 3 and first.json()["data"]["unread_count"] == 3
    assert second.json()["data"]["total"] == 4 and second.json()["data"]["unread_count"] == 4
    assert [item["seq"] for item in second.json()["data"]["items"]] == [1, 2, 3, 4]
    assert len(runtime_probe.list_calls) == 2, "网关不缓存通知权威：每次读取都经 Owner"


# --- B-270：网关不持库 -----------------------------------------------------------------------


def test_b270_notification_modules_hold_no_database() -> None:
    """网关不持库：通知接口/资源服务模块零 sqlalchemy（与架构守卫同一口径）。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
        "apps/im-gateway/src/muad_im_gateway/application/webchat_service.py",
        "apps/im-gateway/src/muad_im_gateway/application/runtime_client.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), relative
