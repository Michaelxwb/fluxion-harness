"""B-254 Gateway 流式上传与动态限额（design §3.4 API-10、§3.7 配置契约；TASK-054）。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/
  错误目录、真实 `api/deps.py` 主体门控依赖图），并挂载**生产** `api/webchat.py` 路由；
  装配根状态（Console/Runtime 客户端、入口快照、设置源、共享存储、启动硬预算）按生产同形注入。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 与 `ConsolePlatformSettingsClient` 打真实本地
  uvicorn 服务（真实 socket）：CHAT 主体核验、bot 快照、平台设置快照（动态限额的权威来源）。
- **真实 Runtime HTTP（Owner）**：`RuntimeClient` 打真实本地 uvicorn 服务；受信登记端口
  `POST /internal/webchat/uploads` 用**真实契约**校验请求体（`WebchatUploadRegistration`），
  记录网关写字节之后上送的权威元信息。
- **真实共享文件系统**：隔离 artifact root 上真实 `NfsArtifactStore` 字节（登记上送的键就是
  磁盘上那份字节）；失败路径不留半成品。
- **真实请求体流**：multipart 体按块产出并记录被消费的块数——"超限在读完之前中止"由它裁决。
- **不读写 DB**：本文件不碰任何数据库；另对上传面模块做零 sqlalchemy 静态断言。

断言要点（B-254）：

- 流式写共享字节 → 经 Runtime 登记权威元信息；响应是 `upload_id` 与元信息（不伪造 artifact_id、
  不回存储键），响应 no-store；
- MIME/字节/数量与**软硬上限一致**：白名单/大小/数量门控复用既有附件词表，限额只来自平台设置
  （Console 权威）并以启动硬预算为上界——设置超预算显式失败，不静默取小值；
- 超额在昂贵处理前拒绝（写完之前中止、清理半成品、不触达登记端口）；
- CHAT 主体与入口当前授权先于任何 IO（设置读取/落字节/登记）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import uvicorn
from fakes import StubConsole
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from muad_artifact_store import NfsArtifactStore
from muad_contracts import BotSnapshotItem, BotSnapshotResponse
from muad_contracts.webchat import WebchatUploadRegistration
from muad_im_gateway.api.webchat import router as webchat_router
from muad_im_gateway.application.bot_snapshot import BotSnapshotCache
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b254"
PLATFORM_USER_ID = UUID("8e3fa042-5d9b-4f6e-a170-7b4c9d3e5f62")
SESSION_ID = UUID("d2b5f903-7c41-4e8b-9f60-1a2b3c4d5e72")
AGENT_ID = UUID("b1c2d3e4-5f6a-4b7c-8d9e-0f1a2b3c4d5e")
OTHER_AGENT_ID = UUID("c2d3e4f5-6a7b-4c8d-9e0f-1a2b3c4d5e6f")
ENTRY_ID = UUID("d3e4f5a6-7b8c-4d9e-8f0a-1b2c3d4e5f70")
OTHER_ENTRY_ID = UUID("e4f5a6b7-8c9d-4e0f-9a1b-2c3d4e5f6071")
DISABLED_ENTRY_ID = UUID("f5a6b7c8-9d0e-4f1a-8b2c-3d4e5f607182")
#: CHAT 会话原文 canary：只允许出现在发往 Console 的请求体里。
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"
#: 上传内容 canary：只允许存在于请求体与共享存储，不进响应/日志。
CONTENT_CANARY = b"B254-CONTENT-CANARY"
#: 启动硬预算（生产装配根发布的 `SharedSettings.chat_upload_hard_max_bytes` 同源值）。
CHAT_UPLOAD_HARD_MAX_BYTES = 52428800

UPLOAD_URL = f"/api/v1/webchat/entries/{ENTRY_ID}/attachments"
UPLOAD_FIELD = "file"
BOUNDARY = "----muad-b254-boundary"
REGISTRATION_PATH = "/internal/webchat/uploads"
CHAT_PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}
UPLOAD_FIELDS = {"upload_id", "kind", "media_type", "size", "filename", "checksum", "expires_at"}

#: 生产路由在真实应用上的注册（TASK-096 装配根只做同一件事）。
app.include_router(webchat_router)


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b254",
        "request_id": "request-b254",
        "timestamp": "2026-10-18T08:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope(None)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


# --- multipart 真实字节流 -------------------------------------------------------------------


def _part(
    name: str,
    data: bytes,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> bytes:
    disposition = f'Content-Disposition: form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    lines = [f"--{BOUNDARY}", disposition]
    if media_type is not None:
        lines.append(f"Content-Type: {media_type}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + data + b"\r\n"


def _multipart_body(*parts: bytes) -> bytes:
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


class _ChunkedBody:
    """真实字节流：按块产出 multipart 体并记录被消费的块数（超限中止的可观测证据）。"""

    def __init__(self, body: bytes, *, chunk_size: int) -> None:
        self._chunks = [body[index : index + chunk_size] for index in range(0, len(body), chunk_size)]
        self.consumed = 0

    @property
    def total(self) -> int:
        return len(self._chunks)

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


def _upload_body(
    data: bytes = b"%PDF-1.7 staged upload",
    *,
    filename: str | None = "报告.pdf",
    media_type: str | None = "application/pdf",
) -> bytes:
    return _multipart_body(_part(UPLOAD_FIELD, data, filename=filename, media_type=media_type))


def _multipart_headers() -> dict[str, str]:
    return {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


# --- 真实 Console Owner 接口桩（真实 socket + 真实封套） ------------------------------------


class _ConsoleProbe:
    """API-12/API-13 的真实 HTTP 桩：chat-principal + bot 快照 + 平台设置快照。"""

    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.settings_calls: list[dict[str, str]] = []
        self.revoked_code: str | None = None
        #: 平台设置快照的 `webchat` 分组：动态限额的唯一来源（Console 权威）。
        self.webchat: dict[str, Any] = {}
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_ID), "key": "webchat-agent", "name": "网页助手"}
        ]
        self.items: list[BotSnapshotItem] = [
            BotSnapshotItem(
                bot_account_id=ENTRY_ID, bot_id=str(ENTRY_ID), channel="WEBCHAT", agent_id=AGENT_ID
            ),
            BotSnapshotItem(
                bot_account_id=OTHER_ENTRY_ID,
                bot_id=str(OTHER_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=OTHER_AGENT_ID,
            ),
            BotSnapshotItem(
                bot_account_id=DISABLED_ENTRY_ID,
                bot_id=str(DISABLED_ENTRY_ID),
                channel="WEBCHAT",
                agent_id=AGENT_ID,
                enabled=False,
            ),
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal
        stub.handlers["/internal/channel/bots"] = self._bots
        stub.handlers[PLATFORM_SETTINGS_PATH] = self._settings

    async def _principal(self, request: Request) -> Response:
        body = json.loads(await request.body() or b"{}")
        self.calls.append((CHAT_PRINCIPAL_PATH, dict(request.headers), body))
        if self.revoked_code is not None:
            return _error(self.revoked_code, 401)
        return _json(
            {
                "principal": {
                    "tenant_id": GATEWAY_TENANT,
                    "platform_user_id": str(PLATFORM_USER_ID),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": list(self.agents),
            }
        )

    async def _bots(self, request: Request) -> Response:
        self.calls.append(("/internal/channel/bots", dict(request.headers), {}))
        return _json(
            BotSnapshotResponse(
                revision="rev-b254", items=self.items, page=1, page_size=20, total=len(self.items)
            ).model_dump(mode="json")
        )

    async def _settings(self, request: Request) -> Response:
        self.settings_calls.append(dict(request.headers))
        return _json({"revision": 7, "settings": {"webchat": dict(self.webchat)}})


# --- 真实 Runtime Owner 接口桩（真实 socket + 真实登记契约） --------------------------------


@dataclass(frozen=True)
class _UploadCall:
    headers: dict[str, str]
    body: dict[str, Any]
    registration: WebchatUploadRegistration


class _RuntimeProbe:
    """Runtime 受信登记端口桩：真实 HTTP；请求体按真实契约校验后才回权威元信息。"""

    def __init__(self) -> None:
        self.calls: list[_UploadCall] = []
        self.reject: tuple[int, str] | None = None
        self.upload_id = uuid.uuid5(uuid.NAMESPACE_URL, f"muad:b254:{uuid.uuid4().hex}")
        self._app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def _build_app(self) -> FastAPI:
        runtime_app = FastAPI()

        @runtime_app.post(REGISTRATION_PATH)
        async def _register(request: Request) -> Response:
            body = json.loads(await request.body() or b"{}")
            # 真实契约校验：网关上送的必须是合法登记请求（多字段/预造引用一律失败）。
            registration = WebchatUploadRegistration.model_validate(body)
            self.calls.append(
                _UploadCall(headers=dict(request.headers), body=body, registration=registration)
            )
            if self.reject is not None:
                return _error(self.reject[1], self.reject[0])
            return _json(
                {
                    "upload_id": str(self.upload_id),
                    "kind": registration.kind,
                    "media_type": registration.media_type,
                    "size": registration.size,
                    "filename": registration.filename,
                    "checksum": registration.checksum,
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                }
            )

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


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


_STATE_NAMES = (
    "console_client",
    "runtime_client",
    "settings_client",
    "artifact_store",
    "chat_upload_hard_max_bytes",
    "bot_snapshot",
    "tenant_id",
)


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    *,
    cookies: dict[str, str] | None = None,
    hard_max_bytes: int = CHAT_UPLOAD_HARD_MAX_BYTES,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console/Runtime 客户端；只注入装配根会发布的状态值。"""
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in _STATE_NAMES}
    console = ConsoleClient(stub_console.url)
    runtime = RuntimeClient(runtime_probe.url)
    settings = ConsolePlatformSettingsClient(stub_console.url, service_token=INTERNAL_TOKEN)
    snapshot = BotSnapshotCache(console, tenant_id=GATEWAY_TENANT)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.settings_client = settings
    app.state.artifact_store = NfsArtifactStore(artifact_root)
    app.state.chat_upload_hard_max_bytes = hard_max_bytes
    app.state.bot_snapshot = snapshot
    app.state.tenant_id = GATEWAY_TENANT
    try:
        await snapshot.refresh()
        assert snapshot.is_ready(), "真实 Console 快照未就绪"
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
        await settings.aclose()


def _stored_files(root: Path) -> list[Path]:
    """隔离 artifact root 下的**字节文件**（不含目录）：失败路径必须一个都不留。"""
    return sorted(path for path in root.rglob("*") if path.is_file())


def _staged_paths(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in _stored_files(root))


# --- B-254：流式写共享字节 → 经 Runtime 登记权威元信息 --------------------------------------


async def test_b254_upload_streams_shared_bytes_then_registers_authoritative_metadata(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """真实 multipart 流 → 共享存储 → 受信登记；响应只有 upload_id 与元信息。"""
    caplog.set_level(logging.DEBUG)
    payload = CONTENT_CANARY + b" " + bytes(range(256)) * 24
    chunked = _ChunkedBody(_upload_body(payload), chunk_size=1024)

    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        response = await client.post(
            UPLOAD_URL, headers=_chat_headers() | _multipart_headers(), content=chunked
        )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert set(data) == UPLOAD_FIELDS, "响应就是 API-10 的权威元信息（无存储键、无预造制品）"
    assert data["kind"] == "DOCUMENT"
    assert data["media_type"] == "application/pdf"
    assert data["size"] == len(payload)
    assert data["filename"] == "报告.pdf"
    assert data["checksum"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert data["upload_id"] == str(runtime_probe.upload_id)
    assert response.headers["cache-control"] == "no-store", "私有聊天资源必须 no-store"
    assert "storage_key" not in response.text and "artifact_id" not in response.text
    assert CONTENT_CANARY.decode() not in response.text
    assert all(CONTENT_CANARY.decode() not in record.getMessage() for record in caplog.records)
    assert chunked.consumed == chunked.total, "成功路径读完整条真实字节流"

    # 完整转 Owner：字节写好后经受信端口登记权威元信息（身份三件套来自受验证主体与入口解析）。
    assert len(runtime_probe.calls) == 1
    call = runtime_probe.calls[0]
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert call.body["user_id"] == str(PLATFORM_USER_ID)
    assert call.body["agent_id"] == str(AGENT_ID)
    assert call.body["entry_id"] == str(ENTRY_ID)
    assert call.body["checksum"] == data["checksum"]
    assert call.body["size"] == len(payload)
    assert call.body["filename"] == "报告.pdf"
    assert "artifact_id" not in call.body and "upload_id" not in call.body
    storage_key = call.body["storage_key"]
    assert storage_key.startswith("webchat/staging/"), "暂存键由系统生成（不含用户文件名）"
    assert "报告" not in storage_key and ".." not in storage_key

    # 真实共享存储：登记上送的键就是磁盘上那份完整字节；临时文件不留。
    assert (artifact_root / storage_key).read_bytes() == payload
    assert _staged_paths(artifact_root) == [storage_key]

    # CHAT 主体只经 Console 核验一次；主体字段不进公开响应。
    assert len([call for call in console_probe.calls if call[0] == CHAT_PRINCIPAL_PATH]) == 1
    assert CHAT_PRINCIPAL_FIELDS.isdisjoint(set(data))
    assert SESSION_TOKEN not in response.text


async def test_b254_upload_gates_chat_identity_and_entry_authorization_before_any_io(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """缺凭据/被撤销/上下文不符/未授权或未知入口：在设置读取与落字节之前就被拒。"""
    body = _upload_body()

    async with _gateway_client(stub_console, runtime_probe, artifact_root, cookies={}) as client:
        unauthorized = await client.post(
            UPLOAD_URL, headers=_chat_headers() | _multipart_headers(), content=body
        )
    assert unauthorized.status_code == 401 and unauthorized.json()["code"] == "UNAUTHORIZED"

    console_probe.revoked_code = "CHAT_SESSION_REVOKED"
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        revoked = await client.post(
            UPLOAD_URL, headers=_chat_headers() | _multipart_headers(), content=body
        )
    assert revoked.status_code == 401 and revoked.json()["code"] == "CHAT_SESSION_REVOKED"

    console_probe.revoked_code = None
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        mismatch = await client.post(
            UPLOAD_URL, headers=_chat_headers(uuid.uuid4()) | _multipart_headers(), content=body
        )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "AUTH_CONTEXT_CHANGED"

    rejected: list[Any] = []
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        for entry_id in (OTHER_ENTRY_ID, uuid.uuid4(), DISABLED_ENTRY_ID):
            rejected.append(
                await client.post(
                    f"/api/v1/webchat/entries/{entry_id}/attachments",
                    headers=_chat_headers() | _multipart_headers(),
                    content=body,
                )
            )
    assert [response.status_code for response in rejected] == [403, 404, 404]
    assert [response.json()["code"] for response in rejected] == [
        "FORBIDDEN",
        "COMMON_NOT_FOUND",
        "COMMON_NOT_FOUND",
    ]

    # 门控先于任何 IO：没有设置读取、没有登记、没有字节。
    assert runtime_probe.calls == []
    assert console_probe.settings_calls == []
    assert _staged_paths(artifact_root) == []
    assert SESSION_TOKEN not in "".join(response.text for response in rejected)


# --- B-254：MIME/字节/数量与软硬上限一致 ----------------------------------------------------


async def test_b254_upload_rejects_oversize_mid_stream_and_cleans_partial_bytes(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """大小上限来自平台设置；超限在**写完之前**中止，半成品清理且不触达登记。"""
    console_probe.webchat = {"upload_max_bytes": 3 * 1024}
    payload = b"z" * (8 * 1024)
    chunked = _ChunkedBody(_upload_body(payload), chunk_size=1024)

    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        response = await client.post(
            UPLOAD_URL, headers=_chat_headers() | _multipart_headers(), content=chunked
        )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ATTACHMENT_TOO_LARGE"
    assert chunked.consumed < chunked.total, (
        f"超限必须在读完整个请求体之前中止（已消费 {chunked.consumed}/{chunked.total} 块）"
    )
    assert _staged_paths(artifact_root) == [], "半成品与临时文件都要清理"
    assert runtime_probe.calls == [], "拒绝路径不得触达登记端口"
    assert console_probe.settings_calls, "限额来自平台设置快照（Console 权威）"


async def test_b254_upload_enforces_mime_count_and_soft_hard_limits(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """白名单 MIME/数量门控复用既有词表；设置超启动硬预算显式失败（不静默取小值）。"""
    console_probe.webchat = {"upload_max_bytes": 1_000_000, "upload_max_per_message": 5}

    # 白名单之外（可执行内容不在允许集合）：拒绝在落字节之前，且不触达登记。
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        unsupported = await client.post(
            UPLOAD_URL,
            headers=_chat_headers() | _multipart_headers(),
            content=_upload_body(media_type="image/svg+xml"),
        )
    assert unsupported.status_code == 400, unsupported.text
    assert unsupported.json()["code"] == "ATTACHMENT_TYPE_NOT_ALLOWED"
    assert _staged_paths(artifact_root) == [] and runtime_probe.calls == []

    # 数量：单请求的文件部件数不得超过 webchat.upload_max_per_message（此处配置 1）。
    console_probe.webchat = {"upload_max_per_message": 1}
    two_files = _multipart_body(
        _part(UPLOAD_FIELD, b"first", filename="a.pdf", media_type="application/pdf"),
        _part(UPLOAD_FIELD, b"second", filename="b.pdf", media_type="application/pdf"),
    )
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        too_many = await client.post(
            UPLOAD_URL, headers=_chat_headers() | _multipart_headers(), content=two_files
        )
    assert too_many.status_code == 400, too_many.text
    assert too_many.json()["code"] == "ATTACHMENT_COUNT_EXCEEDED"
    assert _staged_paths(artifact_root) == [] and runtime_probe.calls == []

    # 恰等于上限放行（边界值与既有附件门控一致）。
    payload = b"exact-limit"
    console_probe.webchat = {"upload_max_bytes": len(payload)}
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        exact = await client.post(
            UPLOAD_URL,
            headers=_chat_headers() | _multipart_headers(),
            content=_upload_body(payload),
        )
    assert exact.status_code == 201, exact.text
    assert exact.json()["data"]["size"] == len(payload)

    # 软上限超启动硬预算：显式失败，不静默按硬预算收下。
    console_probe.webchat = {"upload_max_bytes": 1000}
    async with _gateway_client(
        stub_console, runtime_probe, artifact_root, hard_max_bytes=100
    ) as client:
        over_budget = await client.post(
            UPLOAD_URL,
            headers=_chat_headers() | _multipart_headers(),
            content=_upload_body(b"x" * 200),
        )
    assert over_budget.status_code == 500, over_budget.text
    assert over_budget.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert len(runtime_probe.calls) == 1, "超预算的请求不得登记（上面只有恰等于上限那次）"


async def test_b254_upload_rejects_broken_multipart_shapes_before_registering(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """坏形状（非 multipart/无文件部件/空文件/缺闭合分隔符）显式拒绝，不留字节、不登记。"""
    cases: list[tuple[dict[str, str], bytes]] = [
        ({"Content-Type": "application/octet-stream"}, b"raw-bytes"),
        ({}, b"raw-bytes"),
        (_multipart_headers(), _multipart_body(_part("note", b"text-only"))),
        (
            _multipart_headers(),
            _multipart_body(
                _part(UPLOAD_FIELD, b"", filename="empty.pdf", media_type="application/pdf")
            ),
        ),
        (_multipart_headers(), _upload_body()[:-4]),
    ]
    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        responses = [
            await client.post(UPLOAD_URL, headers=_chat_headers() | headers, content=body)
            for headers, body in cases
        ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422, 422]
    assert [response.json()["code"] for response in responses] == [
        "COMMON_VALIDATION_ERROR",
    ] * 5
    assert _staged_paths(artifact_root) == []
    assert runtime_probe.calls == []


async def test_b254_registration_failure_surfaces_owner_code_and_cleans_staged_bytes(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """登记被 Owner 拒绝：错误码原样上抛，已写字节不留孤儿。"""
    runtime_probe.reject = (403, "FORBIDDEN")
    payload = CONTENT_CANARY + b" conflict"

    async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
        response = await client.post(
            UPLOAD_URL,
            headers=_chat_headers() | _multipart_headers(),
            content=_upload_body(payload),
        )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
    assert len(runtime_probe.calls) == 1, "字节写好后才登记（拒绝来自 Owner）"
    assert runtime_probe.calls[0].registration.size == len(payload)
    assert _staged_paths(artifact_root) == [], "登记失败后不留没有行引用的暂存字节"


async def test_b254_chunk_boundaries_do_not_change_the_stored_bytes(
    stub_console: StubConsole,
    console_probe: _ConsoleProbe,
    runtime_probe: _RuntimeProbe,
    artifact_root: Path,
) -> None:
    """分块边界（含把分隔符/头块劈开）不改变落盘字节与校验和：真实流式解析逐块正确。"""
    payload = CONTENT_CANARY + bytes(range(64))
    body = _upload_body(payload, filename="边界.txt", media_type="text/plain")
    for chunk_size in (1, 2, 3, 7, 64, 4096):
        async with _gateway_client(stub_console, runtime_probe, artifact_root) as client:
            response = await client.post(
                UPLOAD_URL,
                headers=_chat_headers() | _multipart_headers(),
                content=_ChunkedBody(body, chunk_size=chunk_size),
            )
        assert response.status_code == 201, (chunk_size, response.text)
        data = response.json()["data"]
        assert data["size"] == len(payload)
        assert data["checksum"] == "sha256:" + hashlib.sha256(payload).hexdigest()
        assert data["filename"] == "边界.txt"
        storage_key = runtime_probe.calls[-1].body["storage_key"]
        assert (artifact_root / storage_key).read_bytes() == payload

    assert len(_staged_paths(artifact_root)) == 6, "每次上传一个暂存文件，互不覆盖"


# --- B-254：不持库 --------------------------------------------------------------------------


def test_b254_upload_modules_hold_no_database() -> None:
    """网关不持库：上传面模块零 sqlalchemy（与架构守卫同一口径）。"""
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/im-gateway/src/muad_im_gateway/application/webchat_uploads.py",
        "apps/im-gateway/src/muad_im_gateway/api/webchat.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "sqlalchemy" not in source, relative
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("sqlalchemy" not in alias.name for alias in node.names), relative
            elif isinstance(node, ast.ImportFrom):
                assert "sqlalchemy" not in (node.module or ""), relative
